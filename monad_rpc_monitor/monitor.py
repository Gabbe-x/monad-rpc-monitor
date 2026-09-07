"""The monitoring loop: runs probe cycles, persists them, and keeps the latest snapshot."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .alerts import IncidentTracker, Notifier
from .checks import METHOD_PROBES, Sample, NetworkSummary, probe_methods, run_cycle
from .config import Config
from .store import Store

log = logging.getLogger("monitor")


class Monitor:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.tracker = IncidentTracker(store, Notifier(cfg.alerts))
        self._lock = threading.Lock()
        self._latest: list[Sample] = []
        self._summaries: dict[str, NetworkSummary] = {}
        self._cycle = 0
        self._stop = threading.Event()
        self.started_at = time.time()

    # --- one cycle ---------------------------------------------------------

    def run_once(self, *, with_methods: bool = False) -> None:
        t0 = time.perf_counter()
        samples, summaries = run_cycle(self.cfg)
        self.store.add_samples(samples)
        self.tracker.observe(samples)
        with self._lock:
            self._latest = samples
            self._summaries = summaries
            self._cycle += 1
        log.info(
            "cycle %d done in %.1fs: %s",
            self._cycle,
            time.perf_counter() - t0,
            ", ".join(f"{n}: {s.endpoints_healthy}/{s.endpoints_total} healthy @ {s.best_block}" for n, s in summaries.items()),
        )
        if with_methods:
            self._probe_methods(samples, summaries)
        self.store.prune(self.cfg.history_window_h * 3600 * 2)

    def _probe_methods(self, samples: list[Sample], summaries: dict[str, NetworkSummary]) -> None:
        eps = {e.name: e for e in self.cfg.endpoints}
        targets = [s for s in samples if s.status in ("healthy", "slow", "lagging")]

        def work(s: Sample):
            head = summaries[s.network].best_block or s.block or 0
            return s.endpoint, probe_methods(eps[s.endpoint], self.cfg.thresholds, head)

        with ThreadPoolExecutor(max_workers=8) as pool:
            for name, result in pool.map(work, targets):
                self.store.set_methods(name, result)
        log.info("method support probe finished for %d endpoints (%d methods)", len(targets), len(METHOD_PROBES))

    # --- loop --------------------------------------------------------------

    def run_forever(self) -> None:
        every = max(1, self.cfg.thresholds.method_probe_every)
        while not self._stop.is_set():
            started = time.time()
            try:
                self.run_once(with_methods=(self._cycle % every == 0))
            except Exception:  # noqa: BLE001 - keep the loop alive no matter what
                log.exception("cycle failed")
            elapsed = time.time() - started
            self._stop.wait(max(1.0, self.cfg.interval_s - elapsed))

    def stop(self) -> None:
        self._stop.set()

    # --- snapshot for API / page ------------------------------------------

    def snapshot(self) -> dict:
        window_s = self.cfg.history_window_h * 3600
        with self._lock:
            samples = list(self._latest)
            summaries = dict(self._summaries)
        eps = {e.name: e for e in self.cfg.endpoints}
        endpoints = []
        for s in samples:
            up = self.store.uptime(s.endpoint, window_s)
            m_ts, methods = self.store.get_methods(s.endpoint)
            d = s.to_dict()
            d["url"] = eps[s.endpoint].url
            d["provider"] = eps[s.endpoint].provider
            d["uptime_pct"] = round(up.uptime_pct, 2) if up.uptime_pct is not None else None
            d["p50_ms"] = up.p50_ms
            d["p95_ms"] = up.p95_ms
            d["avg_lag"] = round(up.avg_lag, 2) if up.avg_lag is not None else None
            d["samples"] = up.samples
            d["methods"] = methods
            d["methods_ts"] = m_ts
            d["timeline"] = self.store.series(s.endpoint, window_s)
            endpoints.append(d)
        return {
            "generated_at": time.time(),
            "version": __version__,
            "interval_s": self.cfg.interval_s,
            "window_h": self.cfg.history_window_h,
            "thresholds": {
                "lag_blocks": self.cfg.thresholds.lag_blocks,
                "slow_ms": self.cfg.thresholds.slow_ms,
                "consistency_depth": self.cfg.thresholds.consistency_depth,
            },
            "networks": {
                n: {**summaries[n].to_dict(), "chain_id": self.cfg.networks[n].chain_id,
                    "display": self.cfg.networks[n].display or n, "explorer": self.cfg.networks[n].explorer}
                for n in summaries
            },
            "endpoints": endpoints,
            "incidents": self.store.recent_incidents(),
            "method_probes": [{"key": k, "description": d} for k, _, d in METHOD_PROBES],
        }
