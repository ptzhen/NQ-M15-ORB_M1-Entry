"""Volume-weighted average price (VWAP) with standard-deviation or percentage bands, following TradingView's built-in
"Volume-weighted average price" indicator (Pine Script v6, ``ta.vwap``).

What it computes, per anchor period::

    VWAP_t   = sum(volume * src) / sum(volume)                  # cumulative from the start of the period
    stdev_t  = sqrt( sum(volume * src^2) / sum(volume) - VWAP_t^2 )   # volume-weighted standard deviation
    band_k   = VWAP_t +/- multiplier_k * stdev_t                (mode "Standard Deviation")
    band_k   = VWAP_t +/- multiplier_k * 0.01 * VWAP_t          (mode "Percentage": 1 = 1% of VWAP)

The default source is ``hlc3 = (high + low + close) / 3``. A new period starts at the first bar of the data and whenever the
anchor changes. Everything at bar ``t`` uses bars up to and including ``t`` only (no lookahead).

Deliberate differences from TradingView, because this is a futures research repo and not a chart:

* ``Session`` resets at the CME session start (default 18:00 America/New_York) and is keyed to the *trade date*, so the Sunday
  evening session belongs to Monday. On RTH-only data this is identical to a daily reset.
* ``Week`` is the ISO week of the trade date, so a week runs Monday's trade date to Friday's.
* The ``Earnings``, ``Dividends`` and ``Splits`` anchors need corporate-action data that futures do not have: they raise
  ``NotImplementedError`` instead of silently doing something else.
* If the data carries an ``instrument_id`` column, VWAP also resets when the contract changes: a volume-weighted average
  across two different contracts' raw prices (a roll) would be meaningless.
* Pine's "hide on daily or above" and "timeframe gaps" options are chart-display features and are not reproduced.
"""

from __future__ import annotations

import datetime as dt
from typing import Mapping

import numpy as np
import pandas as pd

ANCHORS = ("Session", "Week", "Month", "Quarter", "Year", "Decade", "Century")
UNSUPPORTED_ANCHORS = ("Earnings", "Dividends", "Splits")
BAND_MODES = ("Standard Deviation", "Percentage")
SOURCES = ("hlc3", "close", "open", "high", "low", "hl2", "ohlc4", "hlcc4")


def price_source(df: pd.DataFrame, source: str | pd.Series) -> pd.Series:
    """The price series the average is taken over (TradingView's ``src`` input)."""
    if isinstance(source, pd.Series):
        return source.astype(float)
    o, h, l, c = (df[k].astype(float) if k in df else None for k in ("open", "high", "low", "close"))
    table = {
        "hlc3": lambda: (h + l + c) / 3.0, "hl2": lambda: (h + l) / 2.0, "ohlc4": lambda: (o + h + l + c) / 4.0,
        "hlcc4": lambda: (h + l + c + c) / 4.0, "close": lambda: c, "open": lambda: o, "high": lambda: h, "low": lambda: l,
    }
    if source not in table:
        raise ValueError(f"source must be one of {SOURCES} or a Series, got {source!r}")
    return table[source]()


def _timestamps(df: pd.DataFrame, tz: str) -> pd.DatetimeIndex:
    """Time-zone-aware bar-open timestamps in ``tz``: from ``ts_et``/``ts_event`` columns, else from the index."""
    for col in ("ts_et", "ts_event"):
        if col in df:
            ts = pd.DatetimeIndex(df[col])
            break
    else:
        ts = pd.DatetimeIndex(df.index)
    if ts.tz is None:
        raise ValueError("timestamps must be timezone-aware (UTC or America/New_York); naive times are ambiguous around DST")
    return ts.tz_convert(tz)


def trade_dates(ts_local: pd.DatetimeIndex, session_reset: str) -> pd.DatetimeIndex:
    """Trade date of each bar: the session that starts at ``session_reset`` local time belongs to the NEXT calendar date.
    Wall-clock arithmetic on naive times, so a daylight-saving change cannot shift the boundary."""
    h, m = (int(x) for x in session_reset.split(":"))
    shift = pd.Timedelta(days=1) - pd.Timedelta(hours=h, minutes=m)
    wall = ts_local.tz_localize(None)
    return (wall + shift).normalize()


def period_ids(df: pd.DataFrame, anchor: str, tz: str, session_reset: str) -> np.ndarray:
    """Integer id that increases by one each time a new anchor period (or a new contract) begins."""
    if anchor in UNSUPPORTED_ANCHORS:
        raise NotImplementedError(f"anchor {anchor!r} needs corporate-action data (earnings, dividends, splits) that futures do not have")
    if anchor not in ANCHORS:
        raise ValueError(f"anchor must be one of {ANCHORS}, got {anchor!r}")
    d = trade_dates(_timestamps(df, tz), session_reset)
    keys = {
        "Session": d.to_numpy(),
        "Week": (d.isocalendar().year.to_numpy() * 100 + d.isocalendar().week.to_numpy()),
        "Month": d.year * 100 + d.month,
        "Quarter": d.year * 10 + (d.month - 1) // 3,
        "Year": d.year,
        "Decade": d.year // 10,
        "Century": d.year // 100,
    }[anchor]
    key = pd.Series(np.asarray(keys))
    changed = key.ne(key.shift())
    if "instrument_id" in df:
        changed |= pd.Series(df["instrument_id"].to_numpy()).ne(pd.Series(df["instrument_id"].to_numpy()).shift())
    return changed.cumsum().to_numpy()


def vwap(
    df: pd.DataFrame,
    anchor: str = "Session",
    source: str | pd.Series = "hlc3",
    band_mode: str = "Standard Deviation",
    bands: Mapping[int, float] | None = None,
    offset: int = 0,
    tz: str = "America/New_York",
    session_reset: str = "18:00",
) -> pd.DataFrame:
    """VWAP and bands for time-ordered bars.

    ``df`` needs ``volume``, the columns ``source`` needs (default high/low/close) and timestamps (``ts_et`` / ``ts_event`` column,
    or a tz-aware index). ``bands`` maps a band number to its multiplier (TradingView default: ``{1: 1.0}``; all three:
    ``{1: 1.0, 2: 2.0, 3: 3.0}``). Returns a frame with ``vwap``, ``upper_k``, ``lower_k`` and ``period`` (the anchor-period id),
    indexed like ``df``. ``offset`` shifts the plotted values by that many bars, like Pine's ``offset``.
    """
    if band_mode not in BAND_MODES:
        raise ValueError(f"band_mode must be one of {BAND_MODES}, got {band_mode!r}")
    bands = {1: 1.0} if bands is None else dict(bands)
    if any(m < 0 for m in bands.values()):
        raise ValueError("band multipliers must be >= 0")
    ts = _timestamps(df, tz)
    if not ts.is_monotonic_increasing:
        raise ValueError("bars must be in time order")

    vol = df["volume"].astype(float).to_numpy()
    if np.isnan(vol).any() or (vol < 0).any():
        raise ValueError("volume must be non-negative and not missing")
    if vol.sum() == 0:
        raise ValueError("No volume is provided by the data vendor.")        # Pine raises the same error

    src = price_source(df, source).to_numpy(dtype=float)
    pid = period_ids(df, anchor, tz, session_reset)
    # E[s^2] - E[s]^2 loses almost all precision when prices are large and the spread is small (NQ: 15,000^2 = 2e8 against a variance
    # of tens). The variance is unchanged by subtracting a constant, so centre each period on its first price first; VWAP is
    # recovered by adding it back. Same maths, ~1e8 times more accurate.
    anchor_px = pd.Series(src).groupby(pid).transform("first").to_numpy()
    c = src - anchor_px
    g = pd.DataFrame({"pid": pid, "v": vol, "pv": c * vol, "p2v": c * c * vol})
    cum = g.groupby("pid")[["v", "pv", "p2v"]].cumsum()

    with np.errstate(divide="ignore", invalid="ignore"):
        mean_c = np.where(cum["v"] > 0, cum["pv"] / cum["v"], np.nan)
        w = mean_c + anchor_px                                                 # NaN until the period has traded volume
        variance = np.where(cum["v"] > 0, cum["p2v"] / cum["v"] - mean_c * mean_c, np.nan)
    stdev = np.sqrt(np.clip(variance, 0.0, None))                              # tiny negatives from rounding -> 0
    basis = stdev if band_mode == "Standard Deviation" else w * 0.01

    out = pd.DataFrame({"vwap": w}, index=df.index)
    for k, mult in sorted(bands.items()):
        out[f"upper_{k}"] = w + basis * mult
        out[f"lower_{k}"] = w - basis * mult
    out["period"] = pid
    if offset:
        out = out.assign(**{c: out[c].shift(offset) for c in out.columns if c != "period"})
    return out
