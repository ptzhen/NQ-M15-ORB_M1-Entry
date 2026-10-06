"""Stage 8: the final evaluation. Pure computation on frames it is given, so it can be dry-run on development data.

Procedure and conclusion rules were fixed in docs/DECISIONS.md D8 before the holdout was loaded. ``compute_final`` takes the
chosen-contract bars and the daily table for the WHOLE period it should look at plus a ``split_date``: everything before the
split is "development", everything from the split on is the "holdout". For the real run the split is the config's
``splits.holdout_start``; for the dry run it is an earlier date inside the development data, so no real holdout is touched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from orb.analysis.backtest import enrich
from orb.analysis.metrics import drawdown_stats, group_stats
from orb.analysis.robustness import (
    assign_regime, bootstrap_stat, daily_returns, direction_permutation_test, mean_rows, percentile_ci, prior_volatility,
    profit_factor_rows, sharpe, sharpe_rows, sortino, sortino_rows, win_rate_rows,
)
from orb.execution.simulate import exec_params_from_cfg, simulate
from orb.signals.orb import day_matrices, generate_signals, params_from_cfg

COST_SCALES = (0.0, 1.0, 2.0)


@dataclass
class Period:
    label: str
    dates: pd.DatetimeIndex
    trades: dict            # cost scale -> enriched trade log
    signals: pd.DataFrame
    stats: dict             # headline statistics at 1x with bootstrap intervals
    cost_stats: dict        # cost scale -> {mean_r, lo, hi, total_usd_nq, total_usd_mnq, trades}
    year_stats: pd.DataFrame


def _ci(dist) -> tuple[float, float]:
    return percentile_ci(np.asarray(dist)[~np.isnan(dist)])


def _longest_loss_streak(pnl: pd.Series) -> int:
    win = pnl > 0
    return int((~win).astype(int).groupby(win.cumsum()).cumsum().max()) if len(pnl) else 0


def headline_stats(cfg, tr: pd.DataFrame, dates: pd.DatetimeIndex, rng: np.random.Generator) -> dict:
    """Everything the final table needs for one period at 1x costs. Draw order matches Stage 7 so development intervals reproduce."""
    B, L = cfg.robustness.bootstrap.resamples, cfg.robustness.bootstrap.mean_block_days
    pv = cfg.data.point_value_usd
    r, pnl = tr["r_multiple"].to_numpy(), tr["pnl_usd_nq"].to_numpy()
    dr = daily_returns(tr, dates, pv.NQ).to_numpy()
    d_exp = bootstrap_stat(r, mean_rows, B, L, rng)
    d_win = bootstrap_stat(r, win_rate_rows, B, L, rng)
    d_pf = bootstrap_stat(pnl, profit_factor_rows, B, L, rng)
    d_sh = bootstrap_stat(dr, sharpe_rows, B, L, rng)
    d_so = bootstrap_stat(dr, sortino_rows, B, L, rng)
    dd_usd, dd_mnq, dd_r = drawdown_stats(tr["pnl_usd_nq"]), drawdown_stats(tr["pnl_usd_mnq"]), drawdown_stats(tr["r_multiple"])
    win = tr["pnl_usd_nq"] > 0
    return {
        "trades": len(tr), "longs": int((tr["direction"] > 0).sum()), "shorts": int((tr["direction"] < 0).sum()), "days": len(dates),
        "expectancy_r": (float(r.mean()), *_ci(d_exp)), "win_rate": (float((r > 0).mean()), *_ci(d_win)),
        "profit_factor": (float(pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()), *_ci(d_pf)),
        "sharpe": (sharpe(dr), *_ci(d_sh)), "sortino": (sortino(dr), *_ci(d_so)),
        "avg_win_r": float(tr.loc[win, "r_multiple"].mean()), "avg_loss_r": float(tr.loc[~win, "r_multiple"].mean()),
        "net_points": float(tr["net_points_nq"].sum()), "avg_net_points": float(tr["net_points_nq"].mean()),
        "total_usd_nq": float(tr["pnl_usd_nq"].sum()), "total_usd_mnq": float(tr["pnl_usd_mnq"].sum()),
        "gross_points": float(tr["gross_points"].sum()), "slippage_points": float(tr["slippage_points"].sum()),
        "commission_usd_nq": float(tr["commission_usd_nq"].sum()), "commission_usd_mnq": float(tr["commission_usd_mnq"].sum()),
        "max_dd_usd_nq": dd_usd["max_drawdown"], "max_dd_usd_mnq": dd_mnq["max_drawdown"], "max_dd_r": dd_r["max_drawdown"],
        "longest_underwater_days": dd_usd["longest_underwater_days"],
        "exposure": float(tr["minutes_in_trade"].sum() / (len(dates) * 390)), "avg_minutes": float(tr["minutes_in_trade"].mean()),
        "longest_loss_streak": _longest_loss_streak(tr["pnl_usd_nq"]),
        "p_expectancy_positive": float((d_exp > 0).mean()),
    }


def compute_period(cfg, label: str, bars: pd.DataFrame, dates: pd.DatetimeIndex, seed_offset: int = 0) -> Period:
    orb = params_from_cfg(cfg)
    sig = generate_signals(bars, dates, orb, tz=cfg.session.timezone)
    trades = {}
    for sc in COST_SCALES:
        tr, _ = simulate(bars, sig, orb, exec_params_from_cfg(cfg, cost_scale=sc), tz=cfg.session.timezone)
        trades[sc] = enrich(tr, cfg)
    stats = headline_stats(cfg, trades[1.0], dates, np.random.default_rng(cfg.seed + seed_offset))
    B, L = cfg.robustness.bootstrap.resamples, cfg.robustness.bootstrap.mean_block_days
    cost_stats = {}
    for sc, tr in trades.items():
        rng = np.random.default_rng(cfg.seed + seed_offset + 100 + int(sc))
        r = tr["r_multiple"].to_numpy()
        lo, hi = _ci(bootstrap_stat(r, mean_rows, B, L, rng))
        cost_stats[sc] = {"mean_r": float(r.mean()), "lo": lo, "hi": hi, "total_usd_nq": float(tr["pnl_usd_nq"].sum()),
                          "total_usd_mnq": float(tr["pnl_usd_mnq"].sum()), "trades": len(tr)}
    rows = []
    t1 = trades[1.0]
    for y in sorted(set(t1.index.year)):
        ty = t1[t1.index.year == y]
        r = ty["r_multiple"].to_numpy()
        lo, hi = _ci(bootstrap_stat(r, mean_rows, B, L, np.random.default_rng(cfg.seed + seed_offset + 200 + int(y))))
        rows.append({"year": int(y), "trades": len(ty), "mean_r": float(r.mean()), "lo": lo, "hi": hi, "win_rate": float((r > 0).mean()),
                     "total_usd_nq": float(ty["pnl_usd_nq"].sum()), "total_usd_mnq": float(ty["pnl_usd_mnq"].sum()),
                     "first_date": ty.index.min(), "last_date": ty.index.max()})
    return Period(label, dates, trades, sig, stats, cost_stats, pd.DataFrame(rows))


def volatility_test(cfg, period: Period, bars_all: pd.DataFrame, thresholds: dict) -> dict:
    """Hypothesis D3 on one period with the FROZEN thresholds (never re-estimated)."""
    thr = (thresholds["low_mid_threshold"], thresholds["mid_high_threshold"])
    pv = prior_volatility(bars_all, cfg.robustness.volatility.lookback_days)
    t = period.trades[1.0]
    regime = assign_regime(pv.reindex(t.index), thr)
    B, L = cfg.robustness.bootstrap.resamples, cfg.robustness.bootstrap.mean_block_days
    out = {"thresholds": thr}
    for name in ("low", "mid", "high"):
        r = t.loc[(regime == name).to_numpy(), "r_multiple"].to_numpy()
        if len(r) >= 2:
            lo, hi = _ci(bootstrap_stat(r, mean_rows, B, L, np.random.default_rng(cfg.seed + 300 + len(name))))
            out[name] = {"n": len(r), "mean_r": float(r.mean()), "lo": lo, "hi": hi}
        else:
            out[name] = {"n": len(r), "mean_r": np.nan, "lo": np.nan, "hi": np.nan}
    h = out["high"]
    out["testable"] = h["n"] >= 20
    out["supported"] = bool(out["testable"] and h["lo"] > 0)
    return out


def direction_test(cfg, period: Period, bars: pd.DataFrame) -> dict:
    """Hypothesis D5 on one period: direction-label permutation on hold-to-close moves of the signal days."""
    orb = params_from_cfg(cfg)
    sg = period.signals[period.signals["direction"] != 0]
    mats = day_matrices(bars, pd.DatetimeIndex(sg.index), orb)
    entry_open = sg["entry_open"].to_numpy()
    flat_open = mats["open"][np.arange(len(sg)), orb.n_bars - 1]
    move = (flat_open - entry_open) / entry_open * 1e4
    res = direction_permutation_test(move, sg["direction"].to_numpy(), cfg.robustness.direction_permutations, np.random.default_rng(cfg.seed + 400))
    res["n"] = len(sg)
    res["supported"] = bool(res["p_value"] < 0.05)
    res["supported_bonferroni"] = bool(res["p_value"] < 0.05 / 3)
    return res


def verdict(dev: Period, ho: Period, vol: dict, dirn: dict) -> dict:
    """Apply the D8 conclusion rules mechanically."""
    lo, hi = ho.stats["expectancy_r"][1], ho.stats["expectancy_r"][2]
    primary = "positive and distinguishable" if lo > 0 else ("negative and distinguishable" if hi < 0 else "not distinguishable from zero")
    a = lo > 0
    b = ho.cost_stats[2.0]["mean_r"] > 0
    dev_hi = dev.stats["expectancy_r"][2]
    c = not (dev_hi < 0)
    failed = []
    if not a:
        failed.append("(a) the holdout interval for expectancy at 1x costs is not entirely above zero")
    if not b:
        failed.append("(b) the holdout expectancy is not above zero at 2x costs")
    if not c:
        failed.append("(c) the development result contradicts it: its interval at 1x costs lies entirely below zero")
    return {"primary": primary, "a": a, "b": b, "c": c, "edge": a and b and c, "failed": failed,
            "vol_supported": vol["supported"], "vol_testable": vol["testable"], "dir_supported": dirn["supported"]}


def compute_final(cfg, bars_all: pd.DataFrame, daily_all: pd.DataFrame, split_date, thresholds: dict) -> dict:
    split = pd.Timestamp(split_date)
    dates_all = daily_all.index[daily_all["tradable"]]
    dev_dates, ho_dates = dates_all[dates_all < split], dates_all[dates_all >= split]
    dev = compute_period(cfg, "development", bars_all, dev_dates, seed_offset=0)
    ho = compute_period(cfg, "holdout", bars_all, ho_dates, seed_offset=1000)
    vol = volatility_test(cfg, ho, bars_all, thresholds)
    dirn = direction_test(cfg, ho, bars_all)
    dev_vol = volatility_test(cfg, dev, bars_all, thresholds)
    return {"split": split, "dev": dev, "ho": ho, "vol": vol, "dir": dirn, "dev_vol": dev_vol, "verdict": verdict(dev, ho, vol, dirn)}


def load_thresholds(cfg) -> dict:
    from orb.config import resolve
    return json.loads(resolve(cfg, "logs/frozen_vol_thresholds.json").read_text(encoding="utf-8"))
