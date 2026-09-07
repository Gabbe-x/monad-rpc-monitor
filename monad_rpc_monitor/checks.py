"""Probing logic: per-endpoint health, per-network consensus, cross-endpoint consistency."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from typing import Any

from .config import Config, Endpoint, Thresholds
from .rpc import METHOD_NOT_FOUND, HttpError, RpcError, call, hex_to_int

# Status values, ordered from best to worst. Used for sorting and colouring.
STATUS_ORDER = ["healthy", "slow", "lagging", "rate_limited", "degraded", "wrong_chain", "down"]

# Methods probed during the periodic "method support" check.
# Each entry: (method, params builder taking the latest block number, description)
METHOD_PROBES: list[tuple[str, Any, str]] = [
    ("eth_chainId", lambda bn: [], "chain id"),
    ("eth_blockNumber", lambda bn: [], "head block"),
    ("eth_gasPrice", lambda bn: [], "gas price"),
    ("eth_maxPriorityFeePerGas", lambda bn: [], "priority fee"),
    ("eth_feeHistory", lambda bn: ["0x4", "latest", [25, 75]], "fee history"),
    ("eth_getBlockByNumber", lambda bn: ["latest", False], "block by number"),
    ("eth_getBlockByNumber:finalized", lambda bn: ["finalized", False], "finalized tag"),
    ("eth_getBlockByNumber:safe", lambda bn: ["safe", False], "safe tag"),
    ("eth_getBalance", lambda bn: ["0x0000000000000000000000000000000000000000", "latest"], "balance"),
    ("eth_getCode", lambda bn: ["0x0000000000000000000000000000000000000000", "latest"], "code"),
    ("eth_call", lambda bn: [{"to": "0x0000000000000000000000000000000000000000", "data": "0x"}, "latest"], "call"),
    ("eth_estimateGas", lambda bn: [{"to": "0x0000000000000000000000000000000000000000", "value": "0x0"}], "estimate gas"),
    ("eth_getLogs", lambda bn: [{"fromBlock": hex(max(bn - 5, 0)), "toBlock": hex(bn)}], "logs (6 blocks)"),
    ("eth_getBlockReceipts", lambda bn: [hex(bn)], "block receipts"),
    ("eth_getTransactionCount", lambda bn: ["0x0000000000000000000000000000000000000000", "latest"], "nonce"),
    ("eth_syncing", lambda bn: [], "syncing"),
    ("net_version", lambda bn: [], "net version"),
    ("web3_clientVersion", lambda bn: [], "client version"),
    ("debug_traceBlockByNumber", lambda bn: [hex(bn), {"tracer": "callTracer"}], "debug trace"),
    ("trace_block", lambda bn: [hex(bn)], "parity trace"),
    ("eth_newFilter", lambda bn: [{"fromBlock": "latest"}], "filters"),
    ("eth_subscribe", lambda bn: ["newHeads"], "subscriptions (http)"),
]


@dataclass
class Sample:
    """One probe of one endpoint."""

    endpoint: str
    network: str
    ts: float
    status: str
    latency_ms: float | None = None
    block: int | None = None
    lag: int | None = None
    chain_id: int | None = None
    client: str | None = None
    error: str | None = None
    consistency: str | None = None  # "ok" | "mismatch" | "unavailable" | None (not checked)
    consistency_block: int | None = None
    consistency_hash: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NetworkSummary:
    network: str
    ts: float
    best_block: int | None
    endpoints_total: int
    endpoints_healthy: int
    consistency_block: int | None = None
    canonical_hash: str | None = None
    mismatching: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _probe_basic(ep: Endpoint, thr: Thresholds) -> Sample:
    """Fast probe: chain id, head block, client version. Runs every cycle."""
    ts = time.time()
    sample = Sample(endpoint=ep.name, network=ep.network, ts=ts, status="down")
    try:
        r = call(ep.url, "eth_chainId", timeout=thr.timeout_s, headers=ep.headers)
        sample.chain_id = hex_to_int(r.result)
        r = call(ep.url, "eth_blockNumber", timeout=thr.timeout_s, headers=ep.headers)
        sample.block = hex_to_int(r.result)
        sample.latency_ms = round(r.latency_ms, 1)
    except HttpError as exc:
        sample.status = "rate_limited" if exc.kind == "rate_limited" else "down"
        sample.error = str(exc)
        return sample
    except RpcError as exc:
        sample.status = "degraded"
        sample.error = str(exc)
        return sample
    except (ValueError, TypeError) as exc:
        sample.status = "degraded"
        sample.error = f"bad payload: {exc}"
        return sample

    # client version is informational; failure here does not change status
    try:
        r = call(ep.url, "web3_clientVersion", timeout=thr.timeout_s, headers=ep.headers)
        if isinstance(r.result, str):
            sample.client = r.result[:80]
    except (HttpError, RpcError):
        pass

    sample.status = "healthy"
    return sample


def _probe_block_hash(ep: Endpoint, thr: Thresholds, number: int) -> tuple[str | None, str | None]:
    """Return (hash, error) for block `number` from this endpoint."""
    try:
        r = call(ep.url, "eth_getBlockByNumber", [hex(number), False], timeout=thr.timeout_s, headers=ep.headers)
    except (HttpError, RpcError) as exc:
        return None, str(exc)
    if not isinstance(r.result, dict) or "hash" not in r.result:
        return None, "block not available"
    return str(r.result["hash"]).lower(), None


def probe_methods(ep: Endpoint, thr: Thresholds, head: int) -> dict[str, dict]:
    """Slow probe: which JSON-RPC methods does this endpoint serve?

    Returns {probe_key: {"supported": bool, "latency_ms": float|None, "error": str|None}}.
    A method counts as supported when the node answers without a
    "method not found" error. Argument errors (e.g. an unsupported tag) are reported
    as unsupported with the message preserved, since callers cannot use them anyway.
    """
    out: dict[str, dict] = {}
    for key, params_fn, _desc in METHOD_PROBES:
        method = key.split(":")[0]
        try:
            r = call(ep.url, method, params_fn(head), timeout=thr.timeout_s, headers=ep.headers)
            out[key] = {"supported": True, "latency_ms": round(r.latency_ms, 1), "error": None}
        except RpcError as exc:
            supported = exc.code != METHOD_NOT_FOUND and "not found" not in exc.message.lower() \
                and "not supported" not in exc.message.lower() and "unsupported" not in exc.message.lower()
            out[key] = {"supported": supported, "latency_ms": None, "error": exc.message[:120]}
        except HttpError as exc:
            # transport failures tell us nothing about support; mark as unknown
            out[key] = {"supported": None, "latency_ms": None, "error": str(exc)[:120]}
    return out


def run_cycle(cfg: Config, *, workers: int = 16) -> tuple[list[Sample], dict[str, NetworkSummary]]:
    """Probe every endpoint once and compute per-network summaries."""
    thr = cfg.thresholds
    with ThreadPoolExecutor(max_workers=workers) as pool:
        samples = list(pool.map(lambda ep: _probe_basic(ep, thr), cfg.endpoints))

    by_network: dict[str, list[Sample]] = {}
    for s in samples:
        by_network.setdefault(s.network, []).append(s)

    summaries: dict[str, NetworkSummary] = {}
    ep_by_name = {e.name: e for e in cfg.endpoints}

    for net_name, group in by_network.items():
        expected_chain = cfg.networks[net_name].chain_id
        # Chain-id validation and lag relative to the best observed head.
        for s in group:
            if s.status == "healthy" and s.chain_id is not None and s.chain_id != expected_chain:
                s.status = "wrong_chain"
                s.error = f"chain id {s.chain_id}, expected {expected_chain}"
        heads = [s.block for s in group if s.block is not None and s.status in ("healthy", "slow", "lagging")]
        best = max(heads) if heads else None
        for s in group:
            if s.block is not None and best is not None:
                s.lag = best - s.block
            if s.status == "healthy":
                if s.lag is not None and s.lag > thr.lag_blocks:
                    s.status = "lagging"
                elif s.latency_ms is not None and s.latency_ms > thr.slow_ms:
                    s.status = "slow"

        summary = NetworkSummary(
            network=net_name,
            ts=time.time(),
            best_block=best,
            endpoints_total=len(group),
            endpoints_healthy=sum(1 for s in group if s.status == "healthy"),
        )

        # Cross-endpoint consistency: every responsive endpoint must agree on the hash
        # of a block that is safely behind the best head.
        if best is not None and best > thr.consistency_depth:
            target = best - thr.consistency_depth
            candidates = [s for s in group if s.status in ("healthy", "slow", "lagging")]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(lambda s: _probe_block_hash(ep_by_name[s.endpoint], thr, target), candidates))
            hashes: dict[str, int] = {}
            for s, (h, err) in zip(candidates, results):
                s.consistency_block = target
                if h is None:
                    s.consistency = "unavailable"
                    s.consistency_hash = None
                    if err and s.error is None:
                        s.error = f"consistency check: {err}"
                else:
                    s.consistency_hash = h
                    hashes[h] = hashes.get(h, 0) + 1
            if hashes:
                canonical = max(hashes.items(), key=lambda kv: kv[1])[0]
                summary.consistency_block = target
                summary.canonical_hash = canonical
                for s in candidates:
                    if s.consistency_hash is None:
                        continue
                    if s.consistency_hash == canonical:
                        s.consistency = "ok"
                    else:
                        s.consistency = "mismatch"
                        s.status = "degraded"
                        s.error = f"block {target} hash differs from majority"
                        summary.mismatching.append(s.endpoint)
            summary.endpoints_healthy = sum(1 for s in group if s.status == "healthy")
        summaries[net_name] = summary

    return samples, summaries
