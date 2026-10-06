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
