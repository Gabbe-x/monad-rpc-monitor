"""Static export: write the status page + JSON feed to a directory (e.g. for GitHub Pages)."""

from __future__ import annotations

import json
from pathlib import Path

from . import metrics
from .monitor import Monitor
from .page import STATUS_PAGE


def export(monitor: Monitor, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    (out / "api" / "v1").mkdir(parents=True, exist_ok=True)
    snap = monitor.snapshot()
    snap["static_export"] = True
    (out / "index.html").write_text(STATUS_PAGE)
    (out / "api" / "v1" / "status.json").write_text(json.dumps(snap, indent=1))
    (out / "metrics").write_text(metrics.render(snap))
    (out / ".nojekyll").touch()
    return out
