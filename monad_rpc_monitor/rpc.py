"""Minimal JSON-RPC client over urllib. No third-party dependencies."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

USER_AGENT = "monad-rpc-monitor/0.1 (+https://github.com/Gabbe-x/monad-rpc-monitor)"

# JSON-RPC 2.0 error code for "method not found"
METHOD_NOT_FOUND = -32601


class RpcError(Exception):
    """A JSON-RPC level error returned by the node."""

    def __init__(self, code: int, message: str):
        super().__init__(f"rpc error {code}: {message}")
        self.code = code
        self.message = message


class HttpError(Exception):
    """A transport-level failure (HTTP status, DNS, timeout...)."""

    def __init__(self, kind: str, detail: str, status: int | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status


@dataclass
class RpcResult:
    result: Any
    latency_ms: float


def call(
    url: str,
    method: str,
    params: list | None = None,
    *,
    timeout: float = 8.0,
    headers: dict[str, str] | None = None,
) -> RpcResult:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    hdrs = {"content-type": "application/json", "user-agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        # Some gateways (dRPC, Tenderly...) answer unknown methods with HTTP 4xx and a
        # JSON-RPC error body. Surface that as an RpcError so support detection works.
        try:
            body = exc.read()
            err = json.loads(body).get("error") if body else None
        except Exception:  # noqa: BLE001
            err = None
        if isinstance(err, dict) and status not in (429, 401, 402, 403):
            raise RpcError(int(err.get("code", 0)), str(err.get("message", ""))) from exc
        if status == 429:
            raise HttpError("rate_limited", "HTTP 429 Too Many Requests", status) from exc
        if status in (401, 402, 403):
            raise HttpError("unauthorized", f"HTTP {status}", status) from exc
        raise HttpError("http_error", f"HTTP {status}", status) from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        kind = "timeout" if "timed out" in reason else "unreachable"
        raise HttpError(kind, reason) from exc
    except TimeoutError as exc:
        raise HttpError("timeout", "request timed out") from exc
    except OSError as exc:
        raise HttpError("unreachable", str(exc)) from exc
    latency_ms = (time.perf_counter() - started) * 1000.0

    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise HttpError("bad_response", f"non-JSON body ({len(payload)} bytes)") from exc
    if not isinstance(data, dict):
        raise HttpError("bad_response", "JSON-RPC response is not an object")
    if "error" in data and data["error"] is not None:
        err = data["error"]
        if isinstance(err, dict):
            raise RpcError(int(err.get("code", 0)), str(err.get("message", "")))
        raise RpcError(0, str(err))
    return RpcResult(result=data.get("result"), latency_ms=latency_ms)


def hex_to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16)
    raise ValueError(f"cannot parse {value!r} as hex integer")
