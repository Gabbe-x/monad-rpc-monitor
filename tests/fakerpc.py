"""A tiny scriptable JSON-RPC server used by the tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeNode:
    """Serves a fake EVM node. Behaviour is controlled through public attributes."""

    def __init__(self, chain_id: int = 143, head: int = 1000, *, hash_seed: str = "aa"):
        self.chain_id = chain_id
        self.head = head
        self.hash_seed = hash_seed
        self.http_status: int | None = None  # force an HTTP error code
        self.unsupported: set[str] = {"trace_block"}
        self.client = "FakeNode/1.0"
        self.calls: list[str] = []
        node = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                node.calls.append(body["method"])
                if node.http_status:
                    self.send_response(node.http_status)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(node.handle(body)).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def handle(self, body: dict) -> dict:
        m, p = body["method"], body.get("params", [])
        rid = body.get("id")
        if m in self.unsupported:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
        if m == "eth_chainId":
            return {"jsonrpc": "2.0", "id": rid, "result": hex(self.chain_id)}
        if m == "eth_blockNumber":
            return {"jsonrpc": "2.0", "id": rid, "result": hex(self.head)}
        if m == "web3_clientVersion":
            return {"jsonrpc": "2.0", "id": rid, "result": self.client}
        if m == "eth_getBlockByNumber":
            tag = p[0]
            n = self.head if tag in ("latest", "finalized", "safe") else int(tag, 16)
            if n > self.head:
                return {"jsonrpc": "2.0", "id": rid, "result": None}
            h = "0x" + (self.hash_seed * 32)[:64 - len(hex(n)[2:])] + hex(n)[2:]
            return {"jsonrpc": "2.0", "id": rid, "result": {"number": hex(n), "hash": h}}
        return {"jsonrpc": "2.0", "id": rid, "result": "0x0"}

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
