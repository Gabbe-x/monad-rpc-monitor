# monad-rpc-monitor

Availability, latency, lag and consistency monitor for the public [Monad](https://monad.xyz) RPC endpoints.

**Live status page:** https://gabbe-x.github.io/monad-rpc-monitor/ (rebuilt every 15 minutes from GitHub Actions)

It polls every known keyless public endpoint on Monad **mainnet (chain 143)** and **testnet (chain 10143)** and answers the questions operators, dapp developers and wallet teams keep asking:

* Which public RPCs are up right now, and how fast are they?
* Is an endpoint serving a **stale head** (blocks behind the rest of the network)?
* Do all endpoints **agree on the chain**? A finalized block is fetched from every endpoint and its hash compared against the majority.
* Which **JSON-RPC methods** does each provider actually serve (`eth_getLogs`, `eth_getBlockReceipts`, `debug_traceBlockByNumber`, `finalized` tag, …)?
* Is a provider **rate limiting** free traffic (HTTP 429) rather than down?
* What was the uptime and p50/p95 latency over the last 24 hours?

Everything is exposed three ways: an HTML status page, a JSON feed (`/api/v1/status.json`) and Prometheus metrics (`/metrics`). Optional Telegram / webhook alerts fire on incidents.

Zero third-party dependencies: Python 3.11+ standard library only. One file to configure, one command to run.

## Quick start

```bash
git clone https://github.com/Gabbe-x/monad-rpc-monitor
cd monad-rpc-monitor

# probe everything once and print a table
python3 -m monad_rpc_monitor once

# run the monitor + status page on http://127.0.0.1:8080
python3 -m monad_rpc_monitor serve --listen 127.0.0.1:8080
```

Example of `once` against live endpoints:

```
Monad Mainnet (chain 143)  best head 102816079  healthy 7/10  consistency@102816049: OK
  endpoint                   status          latency       head   lag  client                 error
  monad-foundation           healthy           21 ms  102816079     0  Monad/0.16.1
  ankr                       healthy           33 ms  102816079     0  Monad/0.16.1
  monadinfra                 healthy           51 ms  102816079     0  Monad/0.16.1
  tenderly                   healthy           53 ms  102816079     0  Tenderly/1.0
  monad-foundation-1         healthy           79 ms  102816079     0  Monad/0.16.1
  drpc                       healthy           84 ms  102816079     0  Geth/v10.0.0/drpc
  monad-foundation-2         healthy          167 ms  102816075     4  Monad/0.16.1
  thirdweb                   rate_limited          -          -     -                         rate_limited: HTTP 429 Too Many Requests
  onfinality                 rate_limited          -          -     -                         rate_limited: HTTP 429 Too Many Requests
  blockpi                    down                  -          -     -                         http_error: HTTP 503
```

### Docker

```bash
docker compose up -d
# or
docker build -t monad-rpc-monitor . && docker run -p 8080:8080 -v monitor-data:/data monad-rpc-monitor
```

### pip

```bash
pip install git+https://github.com/Gabbe-x/monad-rpc-monitor
monad-rpc-monitor once
```

A systemd unit is in [`contrib/monad-rpc-monitor.service`](contrib/monad-rpc-monitor.service).

## Commands

| Command | What it does |
| --- | --- |
| `serve` (default) | Runs the probe loop and the HTTP server (status page, JSON API, `/metrics`, `/healthz`). |
| `once [--methods] [--json]` | Probes every endpoint one time and prints a table or the full JSON snapshot. Exit code 0; useful in scripts and cron. |
| `export DIR [--methods] [--db FILE]` | Probes once and writes a static site (`index.html`, `api/v1/status.json`, `metrics`) to `DIR`. This is how the GitHub Pages build works. Pass `--db` to keep history between runs. |
| `check-config` | Validates the TOML configuration. |

Global flags: `-c/--config PATH` (defaults to the bundled `monad_rpc_monitor/endpoints.toml`), `-v/--verbose`.

## How endpoints are classified

Every cycle (30 s by default) each endpoint is asked for `eth_chainId`, `eth_blockNumber` and `web3_clientVersion`. Then, per network:

| Status | Meaning |
| --- | --- |
| `healthy` | Answers, right chain id, within `lag_blocks` of the best head, faster than `slow_ms`. |
| `slow` | Healthy but `eth_blockNumber` took longer than `slow_ms` (1500 ms). |
| `lagging` | Head is more than `lag_blocks` (20) behind the best head seen on any endpoint this cycle. |
| `rate_limited` | HTTP 429. Counted separately so free tiers are not reported as outages. |
| `degraded` | Answers but with a JSON-RPC error, an unparsable payload, or a **block hash that disagrees with the majority**. |
| `wrong_chain` | `eth_chainId` does not match the configured network. |
| `down` | Transport failure: DNS, timeout, connection refused, HTTP 5xx / 4xx. |

**Consistency check.** The block at `best_head - consistency_depth` (30 by default; MonadBFT finalizes in ~2 blocks, the margin covers caching gateways) is fetched from every responsive endpoint. The most common hash is treated as canonical; any endpoint returning a different hash is flagged `degraded` and listed under the network's `mismatching`.

**Method support.** Every `method_probe_every` cycles (6 minutes by default) each responsive endpoint is asked for 22 representative methods and tags. A method counts as supported when the node answers without "method not found" / "not supported" style errors. Gateways that answer unknown methods with HTTP 400 and a JSON-RPC error body (dRPC, Tenderly, …) are handled.

Uptime is the share of `healthy` + `slow` samples in the history window (24 h). Samples are stored in SQLite so figures survive restarts.

## Alerts

An incident opens when an endpoint becomes `down`, `degraded`, `wrong_chain` or `lagging` and closes on recovery. Notifications are sent only when the incident has lasted at least `min_incident_s` (90 s), so a single failed probe never pages anyone.

Configure in `[alerts]` or through environment variables:

| Variable | Purpose |
| --- | --- |
| `MONAD_RPC_MONITOR_TELEGRAM_TOKEN` / `MONAD_RPC_MONITOR_TELEGRAM_CHAT_ID` | Telegram bot notifications |
| `MONAD_RPC_MONITOR_WEBHOOK_URL` | Generic JSON POST (`{"content": ..., "text": ...}`, works with Discord and Slack webhooks) |

Prometheus users can instead scrape `/metrics` and use the rules in [`contrib/prometheus-alerts.yml`](contrib/prometheus-alerts.yml).

## HTTP API

| Path | Content |
| --- | --- |
| `/` | Status page (renders the JSON feed client-side, auto-refreshes). |
| `/api/v1/status.json` | Full snapshot: networks, endpoints with uptime / percentiles / timeline / method support, incidents. |
| `/api/v1/endpoints` | Compact list: one object per endpoint with status, latency, head, lag, uptime. |
| `/metrics` | Prometheus exposition. |
| `/healthz` | 200 once the first cycle completed. |

Field reference: [`docs/API.md`](docs/API.md).

## Configuration

See [`monad_rpc_monitor/endpoints.toml`](monad_rpc_monitor/endpoints.toml). Copy it, edit, and pass with `-c`. Endpoints accept an optional `headers` table for keyed providers:

```toml
[[endpoints]]
name = "my-node"
provider = "self-hosted"
network = "mainnet"
url = "http://10.0.0.5:8080"
headers = { Authorization = "Bearer ..." }
```

Adding a new network is just another `[networks.<name>]` entry with its `chain_id`.

## Development

```bash
python3 -m unittest discover -v     # 25 tests; a fake JSON-RPC node is spun up locally, no network needed
```

Pull requests adding public endpoints are welcome. Please only add endpoints that work without an API key.

## License

MIT
