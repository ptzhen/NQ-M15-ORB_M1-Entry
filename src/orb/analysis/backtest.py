"""Stage 5: run the full backtest and produce the official trade log.

This is the single entry point later stages reuse (walk-forward, sensitivity, bootstrap, holdout):
``run_backtest(cfg, partition, **overrides)``. Data comes only through the holdout-guarded loaders, so
asking for the holdout without the explicit unlock raises.

Note on ``range_minutes`` overrides (used in Stage 6): the day filter requires zero missing bars anywhere
from the open to 15:55 (``max_missing_*_bars = 0``), so a day that is tradable for a 15-minute range is
equally complete for a 5, 30 or 60-minute range. If that tolerance is ever loosened, this must be revisited.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from orb.data.splits import load_bars_chosen, load_daily
from orb.execution.simulate import ExecParams, exec_params_from_cfg, simulate
from orb.signals.orb import ORBParams, generate_signals, params_from_cfg


@dataclass
class BacktestResult:
    trades: pd.DataFrame      # the trade log (enriched), one row per trade, index = trade date
    skipped: pd.DataFrame     # signals that could not be traded, with the reason
    signals: pd.DataFrame     # every day's signal (incl. days with none)
    daily: pd.DataFrame       # the daily table for the partition (flags, chosen contract)
    orb: ORBParams
    ex: ExecParams


def regime_of(dates: pd.DatetimeIndex, cfg) -> pd.Series:
    """Map each date to its regime name from ``cfg.regimes``. Raises if a date is in none or in several,
    so a gap or overlap in the config can never silently drop trades from a table."""
    dates = pd.DatetimeIndex(dates)
    out = pd.Series(pd.NA, index=dates, dtype="object")
    hits = pd.Series(0, index=dates)
    for r in cfg.regimes:
        m = (dates >= pd.Timestamp(r["start"])) & (dates <= pd.Timestamp(r["end"]))
        out[m] = r["name"]
        hits[m] += 1
    if (hits != 1).any():
        bad = dates[(hits != 1).to_numpy()][:5]
        raise ValueError(f"dates not in exactly one regime: {list(bad)}")
    return out


def enrich(trades: pd.DataFrame, cfg) -> pd.DataFrame:
    """Add the descriptive columns the breakdown tables group by."""
    t = trades.copy()
    if len(t) == 0:
        return t
    t["year"] = t.index.year
    t["weekday"] = pd.Categorical(t.index.day_name(), categories=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], ordered=True)
    t["side"] = np.where(t["direction"] > 0, "long", "short")
    t["regime"] = regime_of(t.index, cfg).to_numpy()
    t["minutes_in_trade"] = ((t["exit_time"] - t["entry_time"]).dt.total_seconds() / 60).astype(int)
    t["risk_usd_nq"] = t["risk_points"] * cfg.data.point_value_usd.NQ
    return t


def run_backtest(cfg, partition: str = "dev", purpose: str = "backtest", orb_overrides: dict | None = None, **ex_overrides) -> BacktestResult:
    """Signals -> fills -> trade log for one partition. ``orb_overrides`` change signal parameters
    (e.g. ``range_minutes``); ``ex_overrides`` change execution parameters (e.g. ``target_r``)."""
    orb = params_from_cfg(cfg, **(orb_overrides or {}))
    ex = exec_params_from_cfg(cfg, **ex_overrides)
    daily = load_daily(cfg, partition, purpose=purpose)
    bars = load_bars_chosen(cfg, partition, purpose=purpose)
    dates = daily.index[daily["tradable"]]
    signals = generate_signals(bars, dates, orb, tz=cfg.session.timezone)
    trades, skipped = simulate(bars, signals, orb, ex, tz=cfg.session.timezone)
    return BacktestResult(enrich(trades, cfg), skipped, signals, daily, orb, ex)


def export_trade_log(trades: pd.DataFrame, path) -> None:
    """Write the human-readable trade log (CSV). Times are ISO strings in ET."""
    cols = ["direction", "side", "signal_time", "entry_time", "entry_raw", "entry_fill", "stop", "target", "risk_points",
            "exit_time", "exit_reason", "exit_raw", "exit_fill", "ambiguous_bar", "minutes_in_trade",
            "gross_points", "slippage_points", "price_points", "net_points_nq",
            "commission_usd_nq", "commission_usd_mnq", "pnl_usd_nq", "pnl_usd_mnq", "r_multiple", "r_multiple_mnq",
            "year", "weekday", "regime"]
    out = trades[cols].copy()
    for c in ("signal_time", "entry_time", "exit_time"):
        out[c] = out[c].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    out.index = out.index.strftime("%Y-%m-%d")
    out.index.name = "date"
    out.to_csv(path, float_format="%.4f")
