"""Tiny HTTP server: status page, JSON API, Prometheus metrics, health check."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import metrics
from .monitor import Monitor
from .page import STATUS_PAGE

log = logging.getLogger("http")


def make_handler(monitor: Monitor):
    class Handler(BaseHTTPRequestHandler):
        server_version = "monad-rpc-monitor"

        def log_message(self, fmt, *args):  # quieter than the default
            log.debug("%s " + fmt, self.address_string(), *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.send_header("access-control-allow-origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/":
                self._send(200, STATUS_PAGE.encode(), "text/html; charset=utf-8")
            elif path in ("/api/v1/status.json", "/api/v1/status"):
                self._send(200, json.dumps(monitor.snapshot()).encode(), "application/json")
            elif path == "/api/v1/endpoints":
                snap = monitor.snapshot()
                slim = [
                    {k: e[k] for k in ("endpoint", "network", "provider", "url", "status", "latency_ms", "block", "lag", "uptime_pct", "consistency")}
                    for e in snap["endpoints"]
                ]
                self._send(200, json.dumps({"generated_at": snap["generated_at"], "endpoints": slim}).encode(), "application/json")
            elif path == "/metrics":
                self._send(200, metrics.render(monitor.snapshot()).encode(), "text/plain; version=0.0.4; charset=utf-8")
            elif path == "/healthz":
                ok = bool(monitor.snapshot()["endpoints"])
                self._send(200 if ok else 503, b"ok\n" if ok else b"no data yet\n", "text/plain")
            else:
                self._send(404, b"not found\n", "text/plain")

    return Handler


def serve(monitor: Monitor, listen: str) -> ThreadingHTTPServer:
    host, _, port = listen.rpartition(":")
    srv = ThreadingHTTPServer((host or "0.0.0.0", int(port)), make_handler(monitor))
    log.info("listening on http://%s:%d", *srv.server_address[:2])
    return srv
