# JSON feed and metrics reference

## `GET /api/v1/status.json`

```jsonc
{
  "generated_at": 1788803079.9,      // unix seconds
  "version": "0.1.0",
  "interval_s": 30,
  "window_h": 24,
  "thresholds": { "lag_blocks": 20, "slow_ms": 1500, "consistency_depth": 30 },
  "networks": {
    "mainnet": {
      "network": "mainnet", "chain_id": 143, "display": "Monad Mainnet", "explorer": "https://monadvision.com",
      "ts": 1788803079.1,
      "best_block": 102816187,          // highest head seen this cycle
      "endpoints_total": 10, "endpoints_healthy": 6,
      "consistency_block": 102816157,   // block compared across endpoints
      "canonical_hash": "0x…",          // majority hash at that block
      "mismatching": []                 // endpoint names that returned a different hash
    }
  },
  "endpoints": [
    {
      "endpoint": "monad-foundation", "network": "mainnet", "provider": "Monad Foundation",
      "url": "https://rpc.monad.xyz",
      "ts": 1788803079.0,
      "status": "healthy",              // healthy | slow | lagging | rate_limited | degraded | wrong_chain | down
      "latency_ms": 23.6,               // eth_blockNumber round trip
      "block": 102816187, "lag": 0,
      "chain_id": 143, "client": "Monad/0.16.1",
      "error": null,                    // human readable failure detail
      "consistency": "ok",              // ok | mismatch | unavailable | null (not checked)
      "consistency_block": 102816157, "consistency_hash": "0x…",
      "uptime_pct": 100.0, "p50_ms": 24.1, "p95_ms": 61.0, "avg_lag": 0.1, "samples": 2880,
      "methods": {                      // null until the first method probe ran
        "eth_getLogs": { "supported": true, "latency_ms": 41.2, "error": null },
        "trace_block": { "supported": false, "latency_ms": null, "error": "Method not found" },
        "debug_traceBlockByNumber": { "supported": null, "latency_ms": null, "error": "rate_limited: HTTP 429" }
      },
      "methods_ts": 1788802800.0,
      "timeline": [                     // 48 buckets across the window, oldest first
        { "t": 1788716679.0, "status": "healthy", "latency_ms": 25.0, "n": 60 }
      ]
    }
  ],
  "incidents": [
    { "endpoint": "blockpi", "network": "mainnet", "started": 1788790000.0, "ended": null,
      "status": "down", "detail": "http_error: HTTP 503" }
  ],
  "method_probes": [ { "key": "eth_getLogs", "description": "logs (6 blocks)" } ]
}
```

`supported` in `methods` is `true`, `false` or `null`. `null` means the probe could not tell (transport error, rate limit); it is not counted either way.

Probe keys with a suffix (`eth_getBlockByNumber:finalized`) are the same method called with a specific argument, here the `finalized` block tag.

## `GET /api/v1/endpoints`

Same data, one flat object per endpoint with only: `endpoint, network, provider, url, status, latency_ms, block, lag, uptime_pct, consistency`.

## `GET /metrics`

| Metric | Labels | Meaning |
| --- | --- | --- |
| `monad_rpc_up` | network, endpoint, provider | 1 if `eth_blockNumber` answered on the last probe |
| `monad_rpc_status` | + status | 1 for the current status label, 0 for the others |
| `monad_rpc_latency_ms` | | last `eth_blockNumber` latency |
| `monad_rpc_block_number` | | head reported by the endpoint |
| `monad_rpc_lag_blocks` | | blocks behind the network's best head |
| `monad_rpc_uptime_ratio` | | share of good probes in the window (0..1) |
| `monad_rpc_latency_p95_ms` | | p95 latency in the window |
| `monad_rpc_consistent` | | 1 if the endpoint agrees with the majority hash, 0 on mismatch; absent if not checked |
| `monad_rpc_method_supported` | + method | 1 / 0 per probed method; absent when unknown |
| `monad_network_best_block` | network | best head observed |
| `monad_network_healthy_endpoints` | network | endpoints currently `healthy` |
| `monad_network_endpoints` | network | endpoints configured |
| `monad_rpc_monitor_last_run_timestamp` | | unix time of the last completed cycle |
