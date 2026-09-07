"""Configuration loading (TOML, stdlib only)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Endpoint:
    name: str
    url: str
    network: str
    provider: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Network:
    name: str
    chain_id: int
    display: str = ""
    explorer: str = ""


@dataclass
class Thresholds:
    # blocks behind the best observed head before an endpoint is flagged as lagging
    lag_blocks: int = 20
    # seconds; slower responses are flagged as slow
    slow_ms: int = 1500
    # per-request timeout
    timeout_s: float = 8.0
    # how many blocks below the best head to pick the block used for the consistency check.
    # MonadBFT finality is ~2 blocks, but public gateways may cache; 30 is a safe distance.
    consistency_depth: int = 30
    # how often (in cycles) to run the full method-support probe
    method_probe_every: int = 12


@dataclass
class Config:
    interval_s: int = 30
    history_window_h: int = 24
    listen: str = "127.0.0.1:8080"
    db_path: str = "monad-rpc-monitor.db"
    thresholds: Thresholds = field(default_factory=Thresholds)
    networks: dict[str, Network] = field(default_factory=dict)
    endpoints: list[Endpoint] = field(default_factory=list)
    alerts: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        raw = tomllib.loads(Path(path).read_text())
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        general = raw.get("general", {})
        thr = Thresholds(**raw.get("thresholds", {}))
        networks = {
            key: Network(name=key, **val) for key, val in raw.get("networks", {}).items()
        }
        endpoints: list[Endpoint] = []
        for item in raw.get("endpoints", []):
            ep = Endpoint(**item)
            if ep.network not in networks:
                raise ValueError(f"endpoint {ep.name!r} references unknown network {ep.network!r}")
            endpoints.append(ep)
        names = [e.name for e in endpoints]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate endpoint names: {sorted(dupes)}")
        return cls(
            interval_s=int(general.get("interval_s", 30)),
            history_window_h=int(general.get("history_window_h", 24)),
            listen=str(general.get("listen", "127.0.0.1:8080")),
            db_path=str(general.get("db_path", "monad-rpc-monitor.db")),
            thresholds=thr,
            networks=networks,
            endpoints=endpoints,
            alerts=raw.get("alerts", {}),
        )
