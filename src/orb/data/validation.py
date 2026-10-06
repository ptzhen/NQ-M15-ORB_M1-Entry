"""Structural integrity checks on the raw bars.

These are facts about the file, independent of the strategy. Anything that would make
a backtest silently wrong (duplicate bars, broken OHLC, off-grid prices, one
instrument_id mapping to two symbols) raises in ``assert_integrity``; softer
observations (zero volume, extreme 1-minute ranges) are only reported.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def integrity_report(bars: pd.DataFrame, tick_size: float) -> dict:
    o, h, l, c = (bars[k] for k in ("open", "high", "low", "close"))
    ohlc_bad = (h < l) | (o > h) | (o < l) | (c > h) | (c < l)
    grid_bad = np.zeros(len(bars), dtype=bool)
    for s in (o, h, l, c):
        ticks = s / tick_size
        grid_bad |= (ticks - ticks.round()).abs().to_numpy() > 1e-6

    rel_range = (h - l) / o
    return {
        "rows": int(len(bars)),
        "duplicate_ts_instrument": int(bars.duplicated(["ts_event", "instrument_id"]).sum()),
        "ohlc_invalid": int(ohlc_bad.sum()),
        "non_positive_price": int((bars[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        "off_tick_grid": int(grid_bad.sum()),
        "zero_volume_bars": int((bars["volume"] == 0).sum()),
        "instrument_ids_with_multiple_symbols": int((bars.groupby("instrument_id")["symbol"].nunique() > 1).sum()),
        "ts_sorted": bool(bars["ts_event"].is_monotonic_increasing),
        "bars_range_gt_1pct": int((rel_range > 0.01).sum()),
        "max_1m_range_pct": float(rel_range.max() * 100),
    }


STRUCTURAL = (
    "duplicate_ts_instrument",
    "ohlc_invalid",
    "non_positive_price",
    "off_tick_grid",
    "instrument_ids_with_multiple_symbols",
)


def assert_integrity(report: dict) -> None:
    bad = {k: report[k] for k in STRUCTURAL if report[k]}
    if bad or not report["ts_sorted"]:
        raise ValueError(f"Data integrity failure: {bad}, ts_sorted={report['ts_sorted']}")
