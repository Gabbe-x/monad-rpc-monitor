import json
import threading
import time
import unittest
import urllib.request

from monad_rpc_monitor import metrics
from monad_rpc_monitor.alerts import IncidentTracker, Notifier
from monad_rpc_monitor.checks import Sample
from monad_rpc_monitor.config import Config
from monad_rpc_monitor.monitor import Monitor
from monad_rpc_monitor.server import serve
from monad_rpc_monitor.store import Store

from tests.fakerpc import FakeNode


def sample(ep, status, ts=None, latency=10.0, lag=0, net="net"):
    return Sample(endpoint=ep, network=net, ts=ts or time.time(), status=status, latency_ms=latency, lag=lag, block=1)


class StoreTests(unittest.TestCase):
    def test_uptime_and_percentiles(self):
        st = Store()
        st.add_samples([sample("a", "healthy", latency=10), sample("a", "healthy", latency=30),
                        sample("a", "down", latency=None), sample("a", "slow", latency=2000)])
        u = st.uptime("a", 3600)
        self.assertEqual(u.samples, 4)
        self.assertEqual(u.ok, 3)
        self.assertEqual(u.uptime_pct, 75.0)
        self.assertEqual(u.p50_ms, 30)
        self.assertEqual(u.p95_ms, 2000)

    def test_prune(self):
        st = Store()
        st.add_samples([sample("a", "healthy", ts=time.time() - 10_000), sample("a", "healthy")])
        st.prune(5_000)
        self.assertEqual(st.uptime("a", 100_000).samples, 1)

    def test_series_buckets_worst_status(self):
        st = Store()
        now = time.time()
        st.add_samples([sample("a", "healthy", ts=now - 5), sample("a", "down", ts=now - 4)])
        series = st.series("a", 60, buckets=6)
        self.assertEqual(len(series), 6)
        self.assertEqual(series[-1]["status"], "down")
        self.assertEqual(series[0]["status"], None)

    def test_methods_roundtrip(self):
        st = Store()
        st.set_methods("a", {"eth_chainId": {"supported": True}})
        ts, payload = st.get_methods("a")
        self.assertIsNotNone(ts)
        self.assertTrue(payload["eth_chainId"]["supported"])
        self.assertEqual(st.get_methods("nope"), (None, None))


class IncidentTests(unittest.TestCase):
    def test_open_close_and_notify(self):
        st = Store()
        sent = []
        n = Notifier({"min_incident_s": 0})
        n.send = sent.append  # type: ignore[assignment]
        n.webhook = "http://example.invalid"  # enable
        tr = IncidentTracker(st, n)
        tr.observe([sample("a", "down")])
        self.assertIn("a", st.open_incidents())
        self.assertEqual(len(sent), 1)
        self.assertIn("🔴", sent[0])
        tr.observe([sample("a", "down")])
        self.assertEqual(len(sent), 1, "no duplicate alerts while incident is open")
        tr.observe([sample("a", "healthy")])
        self.assertNotIn("a", st.open_incidents())
        self.assertEqual(len(sent), 2)
        self.assertIn("recovered", sent[1])
        self.assertIsNotNone(st.recent_incidents()[0]["ended"])

    def test_blip_below_threshold_is_not_notified(self):
        st = Store()
        sent = []
        n = Notifier({"min_incident_s": 3600})
        n.send = sent.append  # type: ignore[assignment]
        n.webhook = "http://example.invalid"
        tr = IncidentTracker(st, n)
        tr.observe([sample("a", "down")])
        tr.observe([sample("a", "healthy")])
        self.assertEqual(sent, [])
        self.assertEqual(len(st.recent_incidents()), 1)


class ServerTests(unittest.TestCase):
    def test_end_to_end(self):
        with FakeNode() as a, FakeNode(head=990) as b:
            cfg = Config.from_dict({
                "networks": {"net": {"chain_id": 143, "display": "Test Net"}},
                "thresholds": {"consistency_depth": 5, "timeout_s": 3},
                "endpoints": [{"name": "a", "network": "net", "url": a.url, "provider": "P"},
                              {"name": "b", "network": "net", "url": b.url}],
            })
            mon = Monitor(cfg, Store())
            mon.run_once(with_methods=True)
            srv = serve(mon, "127.0.0.1:0")
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            base = "http://127.0.0.1:%d" % srv.server_address[1]
            try:
                page = urllib.request.urlopen(base + "/").read().decode()
                self.assertIn("Monad RPC Monitor", page)
                snap = json.loads(urllib.request.urlopen(base + "/api/v1/status.json").read())
                self.assertEqual(snap["networks"]["net"]["best_block"], 1000)
                by = {e["endpoint"]: e for e in snap["endpoints"]}
                self.assertEqual(by["a"]["uptime_pct"], 100.0)
                self.assertEqual(by["b"]["lag"], 10)
                self.assertTrue(by["a"]["methods"]["eth_chainId"]["supported"])
                slim = json.loads(urllib.request.urlopen(base + "/api/v1/endpoints").read())
                self.assertEqual(len(slim["endpoints"]), 2)
                prom = urllib.request.urlopen(base + "/metrics").read().decode()
                self.assertIn('monad_rpc_lag_blocks{network="net",endpoint="b",provider=""} 10', prom)
                self.assertIn('monad_rpc_status{network="net",endpoint="a",provider="P",status="healthy"} 1', prom)
                self.assertIn('monad_rpc_method_supported{network="net",endpoint="a",provider="P",method="trace_block"} 0', prom)
                self.assertEqual(urllib.request.urlopen(base + "/healthz").status, 200)
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(base + "/nope")
                self.assertEqual(cm.exception.code, 404)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_metrics_render_without_methods(self):
        snap = {"generated_at": 1.0, "networks": {}, "endpoints": [
            {"network": "n", "endpoint": "e", "provider": "", "status": "down", "block": None,
             "latency_ms": None, "lag": None, "uptime_pct": None, "methods": None}]}
        out = metrics.render(snap)
        self.assertIn('monad_rpc_up{network="n",endpoint="e",provider=""} 0', out)
        self.assertNotIn("monad_rpc_latency_ms{", out)


if __name__ == "__main__":
    unittest.main()
