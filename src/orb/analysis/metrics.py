"""Basic trade-level performance summaries. Stage 8 extends this (Sharpe, drawdown, CIs, ...).

Kept deliberately small now: just enough to show what costs do to the result.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(trades: pd.DataFrame) -> dict:
    """Headline numbers for a trade log (one contract per trade)."""
    if len(trades) == 0:
        return {k: np.nan for k in ("trades", "win_rate", "expectancy_r", "profit_factor", "avg_net_points", "total_usd_nq", "total_usd_mnq")}
    r = trades["r_multiple"]
    wins, losses = trades.loc[trades["pnl_usd_nq"] > 0, "pnl_usd_nq"].sum(), -trades.loc[trades["pnl_usd_nq"] < 0, "pnl_usd_nq"].sum()
    return {
        "trades": int(len(trades)),
        "win_rate": float((trades["pnl_usd_nq"] > 0).mean()),
        "expectancy_r": float(r.mean()),
        "profit_factor": float(wins / losses) if losses > 0 else np.inf,
        "avg_net_points": float(trades["net_points_nq"].mean()),
        "total_usd_nq": float(trades["pnl_usd_nq"].sum()),
        "total_usd_mnq": float(trades["pnl_usd_mnq"].sum()),
    }


def drawdown_stats(pnl: pd.Series) -> dict:
    """Drawdown of a per-trade P&L series (index = trade dates, one per trade).

    The account starts at 0, so the first "peak" is 0. ``max_drawdown`` is the largest fall from the running
    peak of cumulative P&L (a positive number). ``longest_underwater_days`` is the longest calendar span spent
    below the running peak: from a peak date to the date the peak is next reached (or the end of the sample).
    """
    if len(pnl) == 0:
        return {"max_drawdown": 0.0, "longest_underwater_days": 0, "equity": pnl, "drawdown": pnl}
    equity = pnl.cumsum()
    peak = equity.cummax().clip(lower=0.0)
    dd = peak - equity
    # An underwater span runs from one peak to the next (or to the end of the sample) and only exists if at least one
    # observation sits below the peak in between: back-to-back new highs are not a drawdown.
    dates = pd.DatetimeIndex(equity.index)
    pos = [int(i) for i in np.flatnonzero((dd == 0).to_numpy())]
    if dd.iloc[0] > 0:
        pos = [0] + pos                       # first trade already lost money: underwater from day one
    pos.append(len(dates) - 1)
    spans = [(dates[b] - dates[a]).days for a, b in zip(pos[:-1], pos[1:]) if b - a > 1 or (b == len(dates) - 1 and dd.iloc[b] > 0)]
    underwater_days = pd.Series(spans, dtype="int64")
    return {
        "max_drawdown": float(dd.max()),
        "longest_underwater_days": int(underwater_days.max()) if len(underwater_days) else 0,
        "equity": equity,
        "drawdown": dd,
    }


def group_stats(trades: pd.DataFrame, by) -> pd.DataFrame:
    """Per-group summary (numbers, not formatted). ``se_r`` is the naive standard error of mean R
    (treats trades as independent, which understates uncertainty; Stage 7 bootstraps instead)."""
    def f(g: pd.DataFrame) -> pd.Series:
        r = g["r_multiple"]
        gp, gl = g.loc[g["pnl_usd_nq"] > 0, "pnl_usd_nq"].sum(), -g.loc[g["pnl_usd_nq"] < 0, "pnl_usd_nq"].sum()
        return pd.Series({
            "trades": len(g),
            "win_rate": (g["pnl_usd_nq"] > 0).mean(),
            "expectancy_r": r.mean(),
            "se_r": r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan,
            "profit_factor": gp / gl if gl > 0 else np.inf,
            "total_points": g["net_points_nq"].sum(),
            "total_usd_nq": g["pnl_usd_nq"].sum(),
            "total_usd_mnq": g["pnl_usd_mnq"].sum(),
        })
    return trades.groupby(by, observed=True).apply(f, include_groups=False)
