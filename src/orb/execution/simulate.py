"""Execution model: turn Stage 3 signals into filled trades with costs.

Everything here is a deliberate, documented assumption because we only have 1-minute OHLC
(no quotes, no order book). The guiding rule is: **when in doubt, be pessimistic**.

Order of events for one trade (long shown; shorts mirror it):

1. Entry: market order at the OPEN of the entry bar, filled ``slippage`` ticks worse.
2. Levels are set from the ACTUAL fill (as a bracket order placed after the fill would be):
   ``risk R = fill - stop``; ``target = fill + target_r * R``, rounded to the tick grid in the
   direction that makes the target harder to reach.
3. From the entry bar itself (it opens at our price, so its whole range comes after entry)
   through the 15:54 bar, each bar is checked:
     * stop: touched if ``low <= stop``. If a LATER bar *opens* beyond the stop (a gap) we get the
       open, not the stop price: the full gap loss is charged. Fill is ``slippage`` worse.
     * target: touched if ``high >= target`` (or one tick through, if configured). Filled AT the
       target price; a gap above the target is not credited (conservative).
     * if one bar touches both, we cannot know the order inside the minute: the STOP wins.
4. Still open: exit at the open of the 15:55 bar as a market order, ``slippage`` worse.
5. Costs: commission per side per contract on top of slippage.

A trade whose risk is below ``min_risk_ticks`` (e.g. the entry bar opened beyond the stop) is skipped
and reported, never silently dropped.

``simulate`` is vectorised over days; ``simulate_event_driven`` is a slow bar-by-bar twin used by the
tests as an independent reference (same pattern as the signal engine).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from orb.signals.orb import LONG, ORBParams, day_matrices


@dataclass(frozen=True)
class ExecParams:
    tick: float
    point_value: dict
    stop_fraction: float
    target_r: float | None
    target_fill: str                 # "touch" | "through"
    ambiguity: str                   # "stop_first" | "target_first"
    min_risk_ticks: float
    commission_per_side_usd: dict    # unscaled
    slippage_ticks: float            # unscaled
    limit_slippage_ticks: float      # unscaled
    cost_scale: float

    @property
    def slip(self) -> float:
        """Adverse price move per market-type fill, in points, after scaling."""
        return self.slippage_ticks * self.cost_scale * self.tick

    @property
    def limit_slip(self) -> float:
        return self.limit_slippage_ticks * self.cost_scale * self.tick

    def commission_usd_round_trip(self, product: str) -> float:
        return 2.0 * self.commission_per_side_usd[product] * self.cost_scale

    def validate(self) -> None:
        if self.target_fill not in ("touch", "through"):
            raise ValueError("target_fill must be 'touch' or 'through'")
        if self.ambiguity not in ("stop_first", "target_first"):
            raise ValueError("ambiguity must be 'stop_first' or 'target_first'")
        if not 0 < self.stop_fraction <= 1.5:
            raise ValueError("stop_range_fraction must be in (0, 1.5]")
        if self.target_r is not None and self.target_r <= 0:
            raise ValueError("target_r must be positive or None")
        if self.cost_scale < 0:
            raise ValueError("cost_scale must be >= 0")


def exec_params_from_cfg(cfg, **overrides) -> ExecParams:
    """Config -> params. ``overrides`` use the dataclass field names (for sensitivity runs)."""
    e, c = cfg.execution, cfg.execution.costs
    p = ExecParams(
        tick=cfg.data.tick_size,
        point_value={"NQ": cfg.data.point_value_usd.NQ, "MNQ": cfg.data.point_value_usd.MNQ},
        stop_fraction=e.stop_range_fraction,
        target_r=e.target_r,
        target_fill=e.target_fill,
        ambiguity=e.ambiguity,
        min_risk_ticks=e.min_risk_ticks,
        commission_per_side_usd={"NQ": c.commission_per_side_usd.NQ, "MNQ": c.commission_per_side_usd.MNQ},
        slippage_ticks=c.slippage_ticks,
        limit_slippage_ticks=c.limit_slippage_ticks,
        cost_scale=c.cost_scale,
    )
    p = replace(p, **overrides)
    p.validate()
    return p


def _round_away(x: np.ndarray, tick: float, up: np.ndarray) -> np.ndarray:
    """Round to the tick grid: up where ``up`` else down. Used so a rounded level is never *easier*
    to hit than the unrounded one for the trader (targets) and never *harder* (stops)."""
    q = x / tick
    return np.where(up, np.ceil(q - 1e-9), np.floor(q + 1e-9)) * tick


def simulate(bars: pd.DataFrame, signals: pd.DataFrame, orb: ORBParams, ex: ExecParams, tz: str = "America/New_York"):
    """Return (trades, skipped). ``signals`` is the output of ``generate_signals``; ``bars`` the chosen
    contract's RTH bars covering those dates."""
    sig = signals[signals["direction"] != 0]
    n_bars, flat_idx = orb.n_bars, orb.n_bars - 1
    if len(sig) == 0:
        return pd.DataFrame(), pd.DataFrame()

    m = day_matrices(bars, pd.DatetimeIndex(sig.index), orb)
    O, H, L, C = m["open"], m["high"], m["low"], m["close"]
    n = len(sig)
    rows = np.arange(n)
    d = sig["direction"].to_numpy().astype(float)
    is_long = d > 0
    e_idx = sig["entry_idx"].to_numpy().astype(int)
    tick = ex.tick

    entry_raw = O[rows, e_idx]
    entry_fill = entry_raw + d * ex.slip
    width = (sig["range_high"] - sig["range_low"]).to_numpy()
    stop = np.where(is_long, sig["range_high"].to_numpy() - ex.stop_fraction * width,
                    sig["range_low"].to_numpy() + ex.stop_fraction * width)
    stop = _round_away(stop, tick, up=is_long)          # long stop rounded up = closer to price = pessimistic
    risk = d * (entry_fill - stop)
    ok = risk >= ex.min_risk_ticks * tick - 1e-12

    if ex.target_r is None:
        target = np.full(n, np.nan)
    else:
        target = _round_away(entry_fill + d * ex.target_r * risk, tick, up=is_long)  # long target up = further away

    # ---- bar-by-bar checks from the entry bar to the bar BEFORE the time exit ----
    j = np.arange(n_bars)[None, :]
    active = (j >= e_idx[:, None]) & (j < flat_idx)
    stop_hit = np.where(is_long[:, None], L <= stop[:, None], H >= stop[:, None]) & active
    if ex.target_r is None:
        target_hit = np.zeros_like(stop_hit)
    else:
        through = tick if ex.target_fill == "through" else 0.0
        target_hit = np.where(is_long[:, None], H >= (target + through)[:, None], L <= (target - through)[:, None]) & active
    event = stop_hit | target_hit
    has_event = event.any(axis=1)
    k = np.where(has_event, event.argmax(axis=1), flat_idx)          # exit bar index
    both = stop_hit[rows, k] & target_hit[rows, k] & has_event
    take_stop = stop_hit[rows, k] & has_event & ~(both & (ex.ambiguity == "target_first"))
    take_target = has_event & ~take_stop

    open_k = O[rows, k]
    gap = take_stop & (k > e_idx) & np.where(is_long, open_k < stop, open_k > stop)
    stop_raw = np.where(gap, open_k, stop)                            # gap fills at the open
    exit_raw = np.where(take_stop, stop_raw, np.where(take_target, target, O[rows, flat_idx]))
    exit_fill = np.where(take_stop | ~has_event, exit_raw - d * ex.slip, exit_raw - d * ex.limit_slip)
    reason = np.where(take_stop, np.where(gap, "stop_gap", "stop"), np.where(take_target, "target", "time"))

    # ---- P&L ----
    gross_pts = d * (exit_raw - entry_raw)                             # frictionless, same prices
    price_pts = d * (exit_fill - entry_fill)                           # after slippage
    slip_pts = gross_pts - price_pts
    comm = {p: ex.commission_usd_round_trip(p) for p in ("NQ", "MNQ")}
    pv = ex.point_value
    ts = m["ts"]
    out = pd.DataFrame(
        {
            "direction": d.astype(int),
            "signal_time": sig["signal_time"],
            "entry_time": pd.to_datetime(ts[rows, e_idx], utc=True).tz_convert(tz),
            "entry_raw": entry_raw,
            "entry_fill": entry_fill,
            "stop": stop,
            "target": target,
            "risk_points": risk,
            "exit_time": pd.to_datetime(ts[rows, k], utc=True).tz_convert(tz),
            "exit_reason": reason,
            "exit_raw": exit_raw,
            "exit_fill": exit_fill,
            "ambiguous_bar": both,                                    # stop AND target both touched in the exit bar
            "bars_held": k - e_idx,
            "gross_points": gross_pts,
            "slippage_points": slip_pts,
            "price_points": price_pts,
            "commission_usd_nq": comm["NQ"],
            "commission_usd_mnq": comm["MNQ"],
            "pnl_usd_nq": price_pts * pv["NQ"] - comm["NQ"],
            "pnl_usd_mnq": price_pts * pv["MNQ"] - comm["MNQ"],
        },
        index=sig.index,
    )
    out["net_points_nq"] = out["pnl_usd_nq"] / pv["NQ"]
    out["r_multiple"] = out["net_points_nq"] / out["risk_points"]       # R from the NQ cost basis
    out["r_multiple_mnq"] = (out["pnl_usd_mnq"] / pv["MNQ"]) / out["risk_points"]

    trades = out[ok]
    skipped = pd.DataFrame({"direction": d[~ok].astype(int), "risk_points": risk[~ok], "reason": "risk_below_minimum"}, index=sig.index[~ok])
    return trades, skipped


# --------------------------------------------------------------------------------------
# Event-driven twin (slow, deliberately written differently) for testing
# --------------------------------------------------------------------------------------
def _ceil_tick(x, tick):
    return math.ceil(x / tick - 1e-9) * tick


def _floor_tick(x, tick):
    return math.floor(x / tick + 1e-9) * tick


def simulate_event_driven(bars: pd.DataFrame, signals: pd.DataFrame, orb: ORBParams, ex: ExecParams):
    """Walk each trade one bar at a time; only the bars up to the current one are visible."""
    res = {}
    day_bars = {d: g.sort_values("ts_event") for d, g in bars[(bars["mod"] >= orb.open_min) & (bars["mod"] <= orb.flat_min)].groupby("date")}
    flat_idx, tick = orb.n_bars - 1, ex.tick
    for date, s in signals[signals["direction"] != 0].iterrows():
        g = day_bars[date]
        o, h, l = g["open"].to_numpy(), g["high"].to_numpy(), g["low"].to_numpy()
        d, ei = int(s["direction"]), int(s["entry_idx"])
        raw_in = o[ei]
        fill_in = raw_in + d * ex.slip
        if d > 0:
            stop = _ceil_tick(s["range_high"] - ex.stop_fraction * (s["range_high"] - s["range_low"]), tick)
        else:
            stop = _floor_tick(s["range_low"] + ex.stop_fraction * (s["range_high"] - s["range_low"]), tick)
        risk = d * (fill_in - stop)
        if risk < ex.min_risk_ticks * tick - 1e-12:
            continue
        target = None
        if ex.target_r is not None:
            t = fill_in + d * ex.target_r * risk
            target = _ceil_tick(t, tick) if d > 0 else _floor_tick(t, tick)
        exit_raw = exit_fill = reason = None
        both = False
        for i in range(ei, flat_idx):
            hit_stop = (l[i] <= stop) if d > 0 else (h[i] >= stop)
            hit_tgt = False
            if target is not None:
                bar_extreme = h[i] if d > 0 else l[i]
                need = target + d * (tick if ex.target_fill == "through" else 0.0)
                hit_tgt = (bar_extreme >= need) if d > 0 else (bar_extreme <= need)
            if not (hit_stop or hit_tgt):
                continue
            both = hit_stop and hit_tgt
            if hit_stop and not (both and ex.ambiguity == "target_first"):
                gapped = i > ei and ((o[i] < stop) if d > 0 else (o[i] > stop))
                exit_raw = o[i] if gapped else stop
                exit_fill = exit_raw - d * ex.slip
                reason = "stop_gap" if gapped else "stop"
            else:
                exit_raw, exit_fill, reason = target, target - d * ex.limit_slip, "target"
            break
        else:
            exit_raw = o[flat_idx]
            exit_fill = exit_raw - d * ex.slip
            reason = "time"
            i = flat_idx
        price_pts = d * (exit_fill - fill_in)
        res[date] = dict(direction=d, entry_fill=fill_in, stop=stop, target=target if target is not None else np.nan,
                         risk_points=risk, exit_idx=i, exit_reason=reason, exit_raw=exit_raw, exit_fill=exit_fill,
                         ambiguous_bar=both, price_points=price_pts,
                         pnl_usd_nq=price_pts * ex.point_value["NQ"] - ex.commission_usd_round_trip("NQ"))
    out = pd.DataFrame.from_dict(res, orient="index")
    out.index = pd.DatetimeIndex(out.index, name=signals.index.name)
    return out
