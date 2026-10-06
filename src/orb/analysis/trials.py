"""Registry of every strategy variant evaluated, for multiple-testing corrections.

If you try 100 parameter sets and report the best, the best one looks good by luck alone. The
deflated Sharpe ratio and reality-check (Stage 7) need to know HOW MANY variants were tried, so every
evaluated variant is appended here, in a committed file, from the moment tuning could begin.

Cost-scale stress tests and ambiguity bounds are scenarios on ONE variant, not new variants, so they
are not recorded as trials. A change to the signal or exit parameters is.
The registry is idempotent: re-running a stage does not inflate the count.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from orb.config import resolve


def _key(stage: str, name: str, params: dict) -> str:
    return hashlib.sha256(json.dumps([stage, name, params], sort_keys=True, default=str).encode()).hexdigest()[:16]


def record_trial(cfg, stage: str, name: str, params: dict, metrics: dict | None = None) -> bool:
    """Append a trial unless an identical (stage, name, params) one exists. Returns True if new."""
    path = resolve(cfg, cfg.paths.trials_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _key(stage, name, params)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and json.loads(line)["key"] == key:
                return False
    rec = {"key": key, "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "stage": stage,
           "name": name, "params": params, "metrics": metrics or {}}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    return True


def count_trials(cfg) -> int:
    path = resolve(cfg, cfg.paths.trials_log)
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
