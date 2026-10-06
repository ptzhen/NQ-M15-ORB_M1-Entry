"""Config loading.

Every parameter lives in one YAML file so a result can always be traced back to the
exact settings that produced it. The config is exposed as nested attribute access
(``cfg.session.cash_open``) and a ``root`` path that all relative paths hang off.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _to_ns(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in obj.items()})
    return obj


def load_config(path: str | Path | None = None) -> SimpleNamespace:
    """Load the YAML config. ``cfg.root`` is the repo root (parent of ``config/``)."""
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = _to_ns(raw)
    cfg.root = path.resolve().parents[1]
    cfg.config_path = path.resolve()
    return cfg


def resolve(cfg, rel: str) -> Path:
    """Resolve a repo-relative path from the config."""
    return (cfg.root / rel).resolve()


def hhmm_to_minutes(hhmm: str) -> int:
    """'09:30' -> 570. Minute-of-day is how every session boundary is compared, which
    avoids string/time-object comparisons on millions of rows."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)
