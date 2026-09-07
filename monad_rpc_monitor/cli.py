"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .checks import STATUS_ORDER
from .config import Config
from .export import export
from .monitor import Monitor
from .server import serve
from .store import Store

DEFAULT_CONFIG = Path(__file__).resolve().parent / "endpoints.toml"


def _table(snapshot: dict) -> str:
    rows = []
    for net_key, net in snapshot["networks"].items():
        rows.append(f"\n{net['display']} (chain {net['chain_id']})  best head {net['best_block']}  "
                    f"healthy {net['endpoints_healthy']}/{net['endpoints_total']}"
                    + (f"  consistency@{net['consistency_block']}: "
                       + ("OK" if not net["mismatching"] else "MISMATCH " + ",".join(net["mismatching"]))
                       if net.get("canonical_hash") else ""))
        rows.append(f"  {'endpoint':26} {'status':13} {'latency':>9} {'head':>10} {'lag':>5}  {'client':22} error")
        eps = sorted((e for e in snapshot["endpoints"] if e["network"] == net_key),
                     key=lambda e: (STATUS_ORDER.index(e["status"]), e["latency_ms"] or 1e9))
        for e in eps:
            lat = f"{e['latency_ms']:.0f} ms" if e["latency_ms"] is not None else "-"
            rows.append(f"  {e['endpoint']:26} {e['status']:13} {lat:>9} {str(e['block'] or '-'):>10} "
                        f"{str(e['lag'] if e['lag'] is not None else '-'):>5}  {(e['client'] or '')[:22]:22} {e['error'] or ''}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="monad-rpc-monitor",
                                description="Availability, latency and consistency monitor for public Monad RPC endpoints.")
    p.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="path to endpoints.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    s_serve = sub.add_parser("serve", help="run the monitor loop and the HTTP status server (default)")
    s_serve.add_argument("--listen", help="host:port (overrides config)")
    s_serve.add_argument("--db", help="sqlite path (overrides config)")

    s_once = sub.add_parser("once", help="probe every endpoint once and print a table")
    s_once.add_argument("--json", action="store_true", help="print the JSON snapshot instead of a table")
    s_once.add_argument("--methods", action="store_true", help="also run the method-support probe")
    s_once.add_argument("--db", help="sqlite path to record the sample into (default: in-memory)")

    s_exp = sub.add_parser("export", help="probe once and write a static status site")
    s_exp.add_argument("out", help="output directory")
    s_exp.add_argument("--methods", action="store_true")
    s_exp.add_argument("--db", help="sqlite path used to accumulate history between runs")

    sub.add_parser("check-config", help="validate the configuration file")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = Config.load(args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    cmd = args.cmd or "serve"
    if cmd == "check-config":
        print(f"ok: {len(cfg.endpoints)} endpoints across {len(cfg.networks)} networks")
        return 0

    if cmd == "once":
        store = Store(args.db or ":memory:")
        mon = Monitor(cfg, store)
        mon.run_once(with_methods=args.methods)
        print(json.dumps(mon.snapshot(), indent=1) if args.json else _table(mon.snapshot()))
        return 0

    if cmd == "export":
        store = Store(args.db or ":memory:")
        mon = Monitor(cfg, store)
        mon.run_once(with_methods=args.methods)
        out = export(mon, args.out)
        print(f"exported to {out}")
        return 0

    store = Store(args.db or cfg.db_path)
    mon = Monitor(cfg, store)
    srv = serve(mon, args.listen or cfg.listen)
    t = threading.Thread(target=mon.run_forever, name="monitor", daemon=True)
    t.start()

    def _stop(*_):
        logging.getLogger("main").info("shutting down")
        mon.stop()
        srv.shutdown()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    srv.serve_forever()
    return 0
