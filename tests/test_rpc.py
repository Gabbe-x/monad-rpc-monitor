import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from monad_rpc_monitor.rpc import HttpError, RpcError, call, hex_to_int


class _Static:
    """Serve a fixed HTTP status + body."""

    def __init__(self, status: int, body: bytes):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.send_response(outer.status)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(outer.body)

        self.status, self.body = status, body
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return "http://127.0.0.1:%d" % self.srv.server_address[1]

    def __exit__(self, *_):
        self.srv.shutdown()
        self.srv.server_close()


class RpcTests(unittest.TestCase):
    def test_ok(self):
        with _Static(200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x8f"}).encode()) as url:
            r = call(url, "eth_chainId")
        self.assertEqual(hex_to_int(r.result), 143)
        self.assertGreater(r.latency_ms, 0)

    def test_rpc_error_in_200(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}).encode()
        with _Static(200, body) as url, self.assertRaises(RpcError) as cm:
            call(url, "trace_block")
        self.assertEqual(cm.exception.code, -32601)

    def test_rpc_error_in_http_400_is_surfaced(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": 35, "message": "not on free plan"}}).encode()
        with _Static(400, body) as url, self.assertRaises(RpcError) as cm:
            call(url, "trace_block")
        self.assertEqual(cm.exception.code, 35)

    def test_429_is_rate_limited_even_with_json_body(self):
        body = json.dumps({"error": {"code": -32005, "message": "rate limit"}}).encode()
        with _Static(429, body) as url, self.assertRaises(HttpError) as cm:
            call(url, "eth_blockNumber")
        self.assertEqual(cm.exception.kind, "rate_limited")

    def test_plain_5xx(self):
        with _Static(503, b"<html>upstream down</html>") as url, self.assertRaises(HttpError) as cm:
            call(url, "eth_blockNumber")
        self.assertEqual(cm.exception.kind, "http_error")
        self.assertEqual(cm.exception.status, 503)

    def test_non_json_body(self):
        with _Static(200, b"not json") as url, self.assertRaises(HttpError) as cm:
            call(url, "eth_blockNumber")
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_hex_to_int(self):
        self.assertEqual(hex_to_int("0x10"), 16)
        self.assertEqual(hex_to_int(5), 5)
        with self.assertRaises(ValueError):
            hex_to_int(None)


if __name__ == "__main__":
    unittest.main()
