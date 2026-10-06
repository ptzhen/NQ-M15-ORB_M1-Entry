"""Development / holdout partitioning and the holdout guard.

2024-01-01 onward is a final untouched holdout. The risk is not malice, it is drift:
after dozens of runs it is easy to peek "just once". So all strategy-facing access goes
through these loaders. Dev access is free; anything that includes holdout dates raises
unless ``ORB_UNLOCK_HOLDOUT=yes`` is set explicitly, and every unlocked access is
appended to a committed log so the git history shows how often it was touched.

(The data *build* step processes the full date range because it audits file integrity.
It computes no returns and no strategy statistics; it never goes through these loaders.)
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd

from orb.config import resolve

UNLOCK_ENV = "ORB_UNLOCK_HOLDOUT"


class HoldoutLockedError(RuntimeError):
    pass


def _log_access(cfg, what: str, partition: str, purpose: str) -> None:
    path = resolve(cfg, cfg.paths.holdout_log)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\t{what}\t{partition}\t{purpose}\n")


def _partition(df: pd.DataFrame, cfg, partition: str, date_col: str | None, what: str, purpose: str) -> pd.DataFrame:
    if partition not in {"dev", "holdout", "all"}:
        raise ValueError(f"partition must be dev|holdout|all, got {partition!r}")
    dates = df.index if date_col is None else pd.DatetimeIndex(df[date_col])
    cut = pd.Timestamp(cfg.splits.holdout_start)
    if partition == "dev":
        return df[dates < cut]
    if os.environ.get(UNLOCK_ENV) != "yes":
        raise HoldoutLockedError(
            f"Holdout data (>= {cfg.splits.holdout_start}) is locked. It is run once, at the very end. "
            f"Set {UNLOCK_ENV}=yes to unlock; the access will be logged."
        )
    _log_access(cfg, what, partition, purpose)
    return df[dates >= cut] if partition == "holdout" else df


def load_daily(cfg, partition: str = "dev", purpose: str = "") -> pd.DataFrame:
    df = pd.read_parquet(resolve(cfg, cfg.paths.processed_dir) / "daily.parquet")
    return _partition(df, cfg, partition, None, "daily", purpose)


def load_bars_chosen(cfg, partition: str = "dev", purpose: str = "") -> pd.DataFrame:
    df = pd.read_parquet(resolve(cfg, cfg.paths.processed_dir) / "bars_chosen.parquet")
    return _partition(df, cfg, partition, "date", "bars_chosen", purpose)
