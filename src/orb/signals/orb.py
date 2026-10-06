"""Opening Range Breakout signal engine.

Timing convention (the whole point of this module, so it is spelled out):

* A one-minute bar is LABELLED with its open time. The bar labelled 09:44 covers
  09:44:00-09:44:59, so its close is known at 09:45:00.
* The opening range is the bars labelled 09:30 ... 09:44 (``range_minutes`` bars). It is
  complete at 09:45:00.
* A breakout is judged on a bar's CLOSE, so a bar labelled ``t`` can only be acted on once
  it has ended, i.e. at ``t + 1 minute``. The earliest possible signal bar is the one
  labelled 09:45 (its close is known at 09:46:00).
* The trade is entered at the OPEN of the following bar (label ``t + 1``), the first price
  available after the signal bar closed. Nothing from that bar except its open, and nothing
  after it, may influence the signal.

Two independent implementations live here:

* ``generate_signals``  : vectorised over (days x minutes) matrices. This is what the
  backtest uses.
* ``event_driven_signals`` : a bar-by-bar state machine that is *structurally* unable to see
  the future (it is fed one bar at a time). It is slow and exists as the reference the
  vectorised code is tested against, plus perturbation tests, in ``tests/test_signals.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from orb.config import hhmm_to_minutes

LONG, SHORT, NONE = 1, -1, 0


@dataclass(frozen=True)
class ORBParams:
    range_minutes: int      # opening range = first N one-minute bars after the open
    open_min: int           # 09:30 as minute-of-day
    flat_min: int           # 15:55: every position is closed at the open of this bar
    last_entry_min: int     # latest bar label allowed to be an ENTRY bar (15:54)

    @property
    def n_bars(self) -> int:
        """Bars from the open through ``flat_min`` inclusive (the 15:55 bar supplies the exit price)."""
        return self.flat_min - self.open_min + 1

    @property
    def last_entry_idx(self) -> int:
        return self.last_entry_min - self.open_min

    def validate(self) -> None:
        if not 1 <= self.range_minutes < self.last_entry_idx:
            raise ValueError("range_minutes must leave room for at least one signal bar")
        if self.last_entry_min >= self.flat_min:
            raise ValueError("last_entry_bar must be before flat_time, or entry and exit share a bar")


def params_from_cfg(cfg, **overrides) -> ORBParams:
    """Build params from config; ``overrides`` (e.g. range_minutes=30) are for sensitivity runs."""
    p = ORBParams(
        range_minutes=int(overrides.get("range_minutes", cfg.session.range_minutes)),
        open_min=hhmm_to_minutes(cfg.session.cash_open),
        flat_min=hhmm_to_minutes(cfg.session.flat_time),
        last_entry_min=hhmm_to_minutes(overrides.get("last_entry_bar", cfg.signal.last_entry_bar)),
    )
    p.validate()
    return p


# --------------------------------------------------------------------------------------
# Vectorised engine
# --------------------------------------------------------------------------------------
def day_matrices(bars: pd.DataFrame, dates: pd.DatetimeIndex, params: ORBParams) -> dict[str, np.ndarray]:
    """Reshape the chosen-contract bars into (n_days, n_bars) matrices indexed by minute offset
    from the open. Rows follow ``dates``. Raises if any requested day has a missing bar:
    days must already have been filtered to complete ones (``daily['tradable']``), because a
    silent NaN would otherwise turn into a silent non-signal."""
    sel = bars[(bars["mod"] >= params.open_min) & (bars["mod"] <= params.flat_min)]
    di = dates.get_indexer(sel["date"])
    keep = di >= 0
    sel, di = sel[keep], di[keep]
    col = (sel["mod"] - params.open_min).to_numpy()

    out = {k: np.full((len(dates), params.n_bars), np.nan) for k in ("open", "high", "low", "close")}
    for k in out:
        out[k][di, col] = sel[k].to_numpy()
    ts = np.zeros((len(dates), params.n_bars), dtype="int64")
    # pandas 3 may store datetimes at us/ns resolution; force ns so the int64 round-trips exactly.
    ts[di, col] = sel["ts_event"].dt.as_unit("ns").astype("int64").to_numpy()

    missing = int(np.isnan(out["close"]).sum())
    if missing:
        raise ValueError(f"{missing} missing bars in the requested days; filter to tradable days first")
    out["ts"] = ts
    return out


def generate_signals(bars: pd.DataFrame, dates: pd.DatetimeIndex, params: ORBParams, tz: str = "America/New_York") -> pd.DataFrame:
    """One row per date: direction (+1 long, -1 short, 0 none) and, when there is a signal, the
    signal/entry times and prices. ``dates`` must all be complete (tradable) days."""
    dates = pd.DatetimeIndex(dates)
    m = day_matrices(bars, dates, params)
    L, last_sig = params.range_minutes, params.last_entry_idx - 1

    # Range comes ONLY from the first L bars (columns 0..L-1).
    rh = m["high"][:, :L].max(axis=1)
    rl = m["low"][:, :L].min(axis=1)

    # Candidate signal bars: columns L..last_sig, judged on their CLOSE, strictly beyond the range.
    cl = m["close"][:, L : last_sig + 1]
    up, dn = cl > rh[:, None], cl < rl[:, None]
    hit = up | dn
    has = hit.any(axis=1)
    first = hit.argmax(axis=1) + L                       # column of the first breakout bar
    rows = np.arange(len(dates))
    direction = np.where(has, np.where(up[rows, first - L], LONG, SHORT), NONE)

    entry = np.minimum(first + 1, params.n_bars - 1)     # next bar; clipped only so indexing is safe for no-signal rows
    out = pd.DataFrame(
        {
            "direction": direction.astype("int8"),
            "range_high": rh,
            "range_low": rl,
            "range_width": rh - rl,
            "signal_idx": np.where(has, first, -1),
            "signal_close": np.where(has, m["close"][rows, first], np.nan),
            "entry_idx": np.where(has, entry, -1),
            "entry_open": np.where(has, m["open"][rows, entry], np.nan),
        },
        index=dates,
    )
    sig_ts = pd.to_datetime(np.where(has, m["ts"][rows, first], 0), utc=True).tz_convert(tz)
    ent_ts = pd.to_datetime(np.where(has, m["ts"][rows, entry], 0), utc=True).tz_convert(tz)
    out["signal_time"] = pd.Series(sig_ts, index=dates).where(has)
    out["entry_time"] = pd.Series(ent_ts, index=dates).where(has)
    return out


# --------------------------------------------------------------------------------------
# Event-driven reference (cannot see the future by construction)
# --------------------------------------------------------------------------------------
class ORBStateMachine:
    """Consumes one day's bars strictly in time order, one at a time.

    It holds only what a live system would hold at that moment: the running range, and a
    pending signal waiting for the next bar's open. ``on_bar`` is the only way data enters.
    """

    def __init__(self, params: ORBParams):
        self.p = params
        self.rh, self.rl = -np.inf, np.inf
        self.pending: tuple | None = None   # (direction, signal_idx, signal_close)
        self.result: dict | None = None
        self.done = False

    def on_bar(self, idx: int, o: float, h: float, l: float, c: float) -> None:
        if self.done:
            return
        if self.pending is not None:
            # The ONLY thing this bar may contribute is its open, as the entry price.
            d, sidx, sclose = self.pending
            self.result = dict(direction=d, signal_idx=sidx, signal_close=sclose, entry_idx=idx, entry_open=o)
            self.done = True
            return
        if idx < self.p.range_minutes:
            self.rh, self.rl = max(self.rh, h), min(self.rl, l)
            return
        if idx > self.p.last_entry_idx - 1:      # entry bar would fall outside the allowed window
            self.done = True
            return
        if c > self.rh:
            self.pending = (LONG, idx, c)
        elif c < self.rl:
            self.pending = (SHORT, idx, c)


def event_driven_signals(bars: pd.DataFrame, dates: pd.DatetimeIndex, params: ORBParams) -> pd.DataFrame:
    """Reference implementation: same output columns as ``generate_signals`` (minus times)."""
    rows = {}
    g = {d: x for d, x in bars[(bars["mod"] >= params.open_min) & (bars["mod"] <= params.flat_min)].groupby("date")}
    for d in dates:
        sm = ORBStateMachine(params)
        day = g[d].sort_values("ts_event")
        for mod, o, h, l, c in zip(day["mod"], day["open"], day["high"], day["low"], day["close"]):
            sm.on_bar(int(mod) - params.open_min, o, h, l, c)
        r = sm.result or dict(direction=NONE, signal_idx=-1, signal_close=np.nan, entry_idx=-1, entry_open=np.nan)
        r["range_high"], r["range_low"] = sm.rh, sm.rl
        rows[d] = r
    out = pd.DataFrame.from_dict(rows, orient="index")
    out["range_width"] = out["range_high"] - out["range_low"]
    out["direction"] = out["direction"].astype("int8")
    out.index = pd.DatetimeIndex(out.index, name=dates.name)
    return out[["direction", "range_high", "range_low", "range_width", "signal_idx", "signal_close", "entry_idx", "entry_open"]]
