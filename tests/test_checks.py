import unittest

from monad_rpc_monitor.checks import probe_methods, run_cycle
from monad_rpc_monitor.config import Config

from tests.fakerpc import FakeNode


def make_config(nodes: dict[str, FakeNode], chain_id: int = 143, **thr) -> Config:
    return Config.from_dict(
        {
            "general": {"interval_s": 1},
            "thresholds": {"consistency_depth": 5, "timeout_s": 3, **thr},
            "networks": {"net": {"chain_id": chain_id}},
            "endpoints": [{"name": n, "network": "net", "url": node.url} for n, node in nodes.items()],
        }
    )


class RunCycleTests(unittest.TestCase):
    def test_all_healthy_and_consistent(self):
        with FakeNode() as a, FakeNode() as b:
            samples, summaries = run_cycle(make_config({"a": a, "b": b}))
        self.assertEqual({s.status for s in samples}, {"healthy"})
        self.assertEqual({s.consistency for s in samples}, {"ok"})
        self.assertEqual(summaries["net"].best_block, 1000)
        self.assertEqual(summaries["net"].consistency_block, 995)
        self.assertEqual(summaries["net"].mismatching, [])
        for s in samples:
            self.assertEqual(s.lag, 0)
            self.assertEqual(s.client, "FakeNode/1.0")

    def test_lagging_endpoint(self):
        with FakeNode(head=1000) as a, FakeNode(head=900) as b:
            samples, summaries = run_cycle(make_config({"a": a, "b": b}, lag_blocks=20))
        by = {s.endpoint: s for s in samples}
        self.assertEqual(by["a"].status, "healthy")
        self.assertEqual(by["b"].status, "lagging")
        self.assertEqual(by["b"].lag, 100)
        self.assertEqual(summaries["net"].endpoints_healthy, 1)

    def test_wrong_chain(self):
        with FakeNode(chain_id=143) as a, FakeNode(chain_id=10143) as b:
            samples, _ = run_cycle(make_config({"a": a, "b": b}, ))
        by = {s.endpoint: s for s in samples}
        self.assertEqual(by["b"].status, "wrong_chain")
        self.assertIn("expected 143", by["b"].error)
        # a wrong-chain node must not participate in the head / consistency computation
        self.assertEqual(by["a"].consistency, "ok")
        self.assertIsNone(by["b"].consistency)

    def test_hash_mismatch_flags_minority(self):
        with FakeNode(hash_seed="aa") as a, FakeNode(hash_seed="aa") as b, FakeNode(hash_seed="bb") as c:
            samples, summaries = run_cycle(make_config({"a": a, "b": b, "c": c}))
        by = {s.endpoint: s for s in samples}
        self.assertEqual(by["a"].consistency, "ok")
        self.assertEqual(by["c"].consistency, "mismatch")
        self.assertEqual(by["c"].status, "degraded")
        self.assertEqual(summaries["net"].mismatching, ["c"])

    def test_http_errors_map_to_status(self):
        with FakeNode() as a, FakeNode() as b, FakeNode() as c:
            b.http_status = 429
            c.http_status = 503
            samples, summaries = run_cycle(make_config({"a": a, "b": b, "c": c}))
        by = {s.endpoint: s for s in samples}
        self.assertEqual(by["b"].status, "rate_limited")
        self.assertEqual(by["c"].status, "down")
        self.assertIn("503", by["c"].error)
        self.assertEqual(summaries["net"].best_block, 1000)

    def test_unreachable_endpoint(self):
        cfg = Config.from_dict(
            {
                "networks": {"net": {"chain_id": 1}},
                "endpoints": [{"name": "x", "network": "net", "url": "http://127.0.0.1:9"}],
                "thresholds": {"timeout_s": 2},
            }
        )
        samples, summaries = run_cycle(cfg)
        self.assertEqual(samples[0].status, "down")
        self.assertIsNone(summaries["net"].best_block)

    def test_method_probe(self):
        with FakeNode() as a:
            cfg = make_config({"a": a})
            res = probe_methods(cfg.endpoints[0], cfg.thresholds, head=1000)
        self.assertTrue(res["eth_chainId"]["supported"])
        self.assertTrue(res["eth_getBlockByNumber:finalized"]["supported"])
        self.assertFalse(res["trace_block"]["supported"])
        self.assertEqual(res["trace_block"]["error"], "Method not found")
        # every probe key is present
        self.assertEqual(len(res), 22)


class ConfigTests(unittest.TestCase):
    def test_rejects_unknown_network(self):
        with self.assertRaises(ValueError):
            Config.from_dict({"networks": {}, "endpoints": [{"name": "a", "network": "nope", "url": "http://x"}]})

    def test_rejects_duplicate_names(self):
        with self.assertRaises(ValueError):
            Config.from_dict(
                {
                    "networks": {"n": {"chain_id": 1}},
                    "endpoints": [
                        {"name": "a", "network": "n", "url": "http://x"},
                        {"name": "a", "network": "n", "url": "http://y"},
                    ],
                }
            )

    def test_bundled_config_loads(self):
        from monad_rpc_monitor.cli import DEFAULT_CONFIG

        cfg = Config.load(DEFAULT_CONFIG)
        self.assertEqual(cfg.networks["mainnet"].chain_id, 143)
        self.assertEqual(cfg.networks["testnet"].chain_id, 10143)
        self.assertGreater(len(cfg.endpoints), 10)


if __name__ == "__main__":
    unittest.main()
