"""SQLite-backed history so uptime survives restarts. Stdlib only."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass

from .checks import Sample

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL NOT NULL,
    endpoint TEXT NOT NULL,
    network TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL,
    block INTEGER,
    lag INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS samples_ep_ts ON samples(endpoint, ts);
CREATE TABLE IF NOT EXISTS method_support (
    endpoint TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    network TEXT NOT NULL,
    started REAL NOT NULL,
    ended REAL,
    status TEXT NOT NULL,
    detail TEXT
);
"""


@dataclass
class UptimeStats:
    samples: int
    ok: int
    uptime_pct: float | None
    p50_ms: float | None
    p95_ms: float | None
    avg_lag: float | None


class Store:
    def __init__(self, path: str = ":memory:"):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- samples -----------------------------------------------------------

    def add_samples(self, samples: list[Sample]) -> None:
        rows = [
            (s.ts, s.endpoint, s.network, s.status, s.latency_ms, s.block, s.lag, s.error)
            for s in samples
        ]
        with self._lock:
            self._conn.executemany("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?)", rows)
            self._conn.commit()

    def prune(self, older_than_s: float) -> None:
        cutoff = time.time() - older_than_s
        with self._lock:
            self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM incidents WHERE ended IS NOT NULL AND ended < ?", (cutoff,))
            self._conn.commit()

    def uptime(self, endpoint: str, window_s: float) -> UptimeStats:
        cutoff = time.time() - window_s
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, latency_ms, lag FROM samples WHERE endpoint=? AND ts>=?",
                (endpoint, cutoff),
            ).fetchall()
        n = len(rows)
        ok = sum(1 for st, _, _ in rows if st in ("healthy", "slow"))
        lats = sorted(l for _, l, _ in rows if l is not None)
        lags = [g for _, _, g in rows if g is not None]

        def pct(p: float) -> float | None:
            if not lats:
                return None
            idx = min(len(lats) - 1, int(round(p * (len(lats) - 1))))
            return lats[idx]

        return UptimeStats(
            samples=n,
            ok=ok,
            uptime_pct=(100.0 * ok / n) if n else None,
            p50_ms=pct(0.5),
            p95_ms=pct(0.95),
            avg_lag=(sum(lags) / len(lags)) if lags else None,
        )

    def series(self, endpoint: str, window_s: float, buckets: int = 48) -> list[dict]:
        """Down-sampled timeline for sparklines: per bucket, worst status + median latency."""
        now = time.time()
        cutoff = now - window_s
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, status, latency_ms FROM samples WHERE endpoint=? AND ts>=? ORDER BY ts",
                (endpoint, cutoff),
            ).fetchall()
        width = window_s / buckets
        out = [{"t": cutoff + i * width, "status": None, "latency_ms": None, "n": 0} for i in range(buckets)]
        acc: list[list[float]] = [[] for _ in range(buckets)]
        from .checks import STATUS_ORDER

        for ts, st, lat in rows:
            i = min(buckets - 1, int((ts - cutoff) / width))
            b = out[i]
            b["n"] += 1
            if b["status"] is None or STATUS_ORDER.index(st) > STATUS_ORDER.index(b["status"]):
                b["status"] = st
            if lat is not None:
                acc[i].append(lat)
        for i, b in enumerate(out):
            if acc[i]:
                srt = sorted(acc[i])
                b["latency_ms"] = srt[len(srt) // 2]
        return out

    # --- method support ----------------------------------------------------

    def set_methods(self, endpoint: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO method_support VALUES (?,?,?)",
                (endpoint, time.time(), json.dumps(payload)),
            )
            self._conn.commit()

    def get_methods(self, endpoint: str) -> tuple[float | None, dict | None]:
        with self._lock:
            row = self._conn.execute(
                "SELECT ts, payload FROM method_support WHERE endpoint=?", (endpoint,)
            ).fetchone()
        if not row:
            return None, None
        return row[0], json.loads(row[1])

    # --- incidents ---------------------------------------------------------

    def open_incident(self, endpoint: str, network: str, status: str, detail: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO incidents(endpoint, network, started, status, detail) VALUES (?,?,?,?,?)",
                (endpoint, network, time.time(), status, detail),
            )
            self._conn.commit()

    def close_incident(self, endpoint: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE incidents SET ended=? WHERE endpoint=? AND ended IS NULL", (time.time(), endpoint)
            )
            self._conn.commit()

    def open_incidents(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT endpoint, network, started, status, detail FROM incidents WHERE ended IS NULL"
            ).fetchall()
        return {r[0]: {"network": r[1], "started": r[2], "status": r[3], "detail": r[4]} for r in rows}

    def recent_incidents(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT endpoint, network, started, ended, status, detail FROM incidents "
                "ORDER BY started DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"endpoint": r[0], "network": r[1], "started": r[2], "ended": r[3], "status": r[4], "detail": r[5]}
            for r in rows
        ]
