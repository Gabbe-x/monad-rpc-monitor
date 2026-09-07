"""Incident tracking and optional notifications (Telegram / generic webhook)."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request

from .checks import Sample
from .store import Store

log = logging.getLogger("alerts")

BAD = {"down", "degraded", "wrong_chain", "lagging"}


class Notifier:
    def __init__(self, settings: dict):
        self.telegram_token = settings.get("telegram_bot_token") or os.environ.get("MONAD_RPC_MONITOR_TELEGRAM_TOKEN")
        self.telegram_chat = settings.get("telegram_chat_id") or os.environ.get("MONAD_RPC_MONITOR_TELEGRAM_CHAT_ID")
        self.webhook = settings.get("webhook_url") or os.environ.get("MONAD_RPC_MONITOR_WEBHOOK_URL")
        self.min_incident_s = float(settings.get("min_incident_s", 90))

    @property
    def enabled(self) -> bool:
        return bool((self.telegram_token and self.telegram_chat) or self.webhook)

    def send(self, text: str) -> None:
        if self.telegram_token and self.telegram_chat:
            self._post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                {"chat_id": self.telegram_chat, "text": text, "disable_web_page_preview": True},
            )
        if self.webhook:
            self._post(self.webhook, {"content": text, "text": text})

    @staticmethod
    def _post(url: str, payload: dict) -> None:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers={"content-type": "application/json"}, method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as exc:  # noqa: BLE001 - notification failures must never stop monitoring
            log.warning("notification failed: %s", exc)


class IncidentTracker:
    """Turns a stream of samples into open/closed incidents and fires notifications.

    An endpoint enters an incident when it reports a BAD status; the incident is closed
    once it is healthy (or merely slow / rate limited) again. Notifications are delayed
    by `min_incident_s` so that single failed probes do not page anyone.
    """

    def __init__(self, store: Store, notifier: Notifier):
        self.store = store
        self.notifier = notifier
        self._pending: dict[str, float] = {}  # endpoint -> first bad ts (not yet notified)
        self._notified: set[str] = set()
        for name in store.open_incidents():
            self._notified.add(name)

    def observe(self, samples: list[Sample]) -> None:
        open_now = self.store.open_incidents()
        now = time.time()
        for s in samples:
            is_bad = s.status in BAD
            if is_bad and s.endpoint not in open_now:
                self.store.open_incident(s.endpoint, s.network, s.status, s.error)
                self._pending.setdefault(s.endpoint, now)
            elif is_bad and s.endpoint in open_now:
                self._pending.setdefault(s.endpoint, open_now[s.endpoint]["started"])
            elif not is_bad and s.endpoint in open_now:
                self.store.close_incident(s.endpoint)
                started = open_now[s.endpoint]["started"]
                self._pending.pop(s.endpoint, None)
                if s.endpoint in self._notified:
                    self._notified.discard(s.endpoint)
                    self._notify(
                        f"✅ {s.network}/{s.endpoint} recovered after {int(now - started)}s "
                        f"(block {s.block}, {s.latency_ms} ms)"
                    )
            if is_bad and s.endpoint in self._pending and s.endpoint not in self._notified:
                if now - self._pending[s.endpoint] >= self.notifier.min_incident_s:
                    self._notified.add(s.endpoint)
                    self._notify(f"🔴 {s.network}/{s.endpoint} is {s.status}: {s.error or 'no detail'}")

    def _notify(self, text: str) -> None:
        log.info("%s", text)
        if self.notifier.enabled:
            self.notifier.send(text)
