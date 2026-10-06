"""Execution-model tests: hand-computed trades, edge cases, and a bar-by-bar twin.

Hand-built days (``quiet_day`` from test_signals): range 99..101, a long breakout on the 09:45 bar
(idx 15) closing 101.5, entry at the open of idx 16 = 101.75. With zero costs the stop is 99, so
R = 2.75 and a 1R target is 104.5. All prices are on the 0.25 tick grid.
"""

import numpy as np
import pandas as pd
import pytest

from orb.execution.simulate import exec_params_from_cfg, simulate, simulate_event_driven
from orb.signals.orb import generate_signals, params_from_cfg
from test_signals import quiet_day, random_days

LONG_SIGNAL = dict(b15=dict(close=101.5, high=101.5), b16=dict(open=101.75))
SHORT_SIGNAL = dict(b15=dict(close=98.5, low=98.5), b16=dict(open=98.25))


@pytest.fixture
def orb(cfg):
    return params_from_cfg(cfg)


def trade(cfg, orb, day, **ex_overrides):
    ex = exec_params_from_cfg(cfg, **ex_overrides)
    dates = pd.DatetimeIndex(sorted(day["date"].unique()))
    sig = generate_signals(day, dates, orb)
    trades, skipped = simulate(day, sig, orb, ex)
    return trades, skipped


def one(cfg, orb, overrides=None, **ex):
    ov = dict(LONG_SIGNAL)
    ov.update(overrides or {})
    t, s = trade(cfg, orb, quiet_day("2023-06-05", **ov), **ex)
    return t.iloc[0] if len(t) else None, s


# ---------------------------------------------------------------- hand-computed trades (zero costs)
def test_target_hit_long(cfg, orb):
    t, _ = one(cfg, orb, dict(b20=dict(high=105.0)), cost_scale=0.0)
    assert t["exit_reason"] == "target" and t["exit_raw"] == 104.5
    assert t["risk_points"] == 2.75 and t["target"] == 104.5
    assert t["gross_points"] == pytest.approx(2.75) and t["r_multiple"] == pytest.approx(1.0)
    assert t["bars_held"] == 4                                   # idx 16 -> 20


def test_stop_hit_long(cfg, orb):
    t, _ = one(cfg, orb, dict(b20=dict(low=98.5)), cost_scale=0.0)
    assert t["exit_reason"] == "stop" and t["exit_raw"] == 99.0
    assert t["r_multiple"] == pytest.approx(-1.0)


def test_both_touched_in_one_bar_stop_wins_and_is_flagged(cfg, orb):
    day = dict(b20=dict(high=105.0, low=98.0))
    t, _ = one(cfg, orb, day, cost_scale=0.0)
    assert t["exit_reason"] == "stop" and bool(t["ambiguous_bar"]) and t["r_multiple"] == pytest.approx(-1.0)
    t2, _ = one(cfg, orb, day, cost_scale=0.0, ambiguity="target_first")     # optimistic bound
    assert t2["exit_reason"] == "target" and t2["r_multiple"] == pytest.approx(1.0)


def test_gap_through_stop_fills_at_the_open_not_the_stop(cfg, orb):
    t, _ = one(cfg, orb, dict(b25=dict(open=97.0, high=97.0, low=96.5, close=97.0)), cost_scale=0.0)
    assert t["exit_reason"] == "stop_gap" and t["exit_raw"] == 97.0
    assert t["r_multiple"] == pytest.approx((97.0 - 101.75) / 2.75)           # worse than -1R


def test_time_exit_at_the_open_of_the_1555_bar(cfg, orb):
    t, _ = one(cfg, orb, dict(b385=dict(open=102.0)), cost_scale=0.0)
    assert t["exit_reason"] == "time" and t["exit_raw"] == 102.0
    assert t["exit_time"].strftime("%H:%M") == "15:55"
    assert t["gross_points"] == pytest.approx(0.25)


def test_short_mirrors_long(cfg, orb):
    tgt, _ = one(cfg, orb, dict(SHORT_SIGNAL, b20=dict(low=95.0)), cost_scale=0.0)
    assert tgt["direction"] == -1 and tgt["stop"] == 101.0 and tgt["risk_points"] == 2.75
    assert tgt["exit_reason"] == "target" and tgt["exit_raw"] == 95.5 and tgt["r_multiple"] == pytest.approx(1.0)
    stp, _ = one(cfg, orb, dict(SHORT_SIGNAL, b20=dict(high=101.5)), cost_scale=0.0)
    assert stp["exit_reason"] == "stop" and stp["r_multiple"] == pytest.approx(-1.0)


def test_stop_checked_on_the_entry_bar_itself(cfg, orb):
    t, _ = one(cfg, orb, dict(b16=dict(open=101.75, low=98.5)), cost_scale=0.0)
    assert t["exit_reason"] == "stop" and t["bars_held"] == 0


def test_target_through_needs_one_extra_tick(cfg, orb):
    touch, _ = one(cfg, orb, dict(b20=dict(high=104.5)), cost_scale=0.0, target_fill="touch")
    thru, _ = one(cfg, orb, dict(b20=dict(high=104.5)), cost_scale=0.0, target_fill="through")
    thru2, _ = one(cfg, orb, dict(b20=dict(high=104.75)), cost_scale=0.0, target_fill="through")
    assert touch["exit_reason"] == "target" and thru["exit_reason"] == "time" and thru2["exit_reason"] == "target"


def test_no_target_means_stop_or_time_only(cfg, orb):
    t, _ = one(cfg, orb, dict(b20=dict(high=110.0)), cost_scale=0.0, target_r=None)
    assert t["exit_reason"] == "time" and np.isnan(t["target"])


def test_target_rounds_to_the_tick_away_from_price(cfg, orb):
    # R = 2.75; 1.5R = 4.125 -> 105.875 -> long target rounds UP to 106.0 (harder to reach)
    t, _ = one(cfg, orb, cost_scale=0.0, target_r=1.5)
    assert t["target"] == 106.0
    s, _ = one(cfg, orb, dict(SHORT_SIGNAL), cost_scale=0.0, target_r=1.5)   # 98.25 - 4.125 = 94.125 -> DOWN to 94.0
    assert s["target"] == 94.0


def test_midpoint_stop_variant(cfg, orb):
    t, _ = one(cfg, orb, cost_scale=0.0, stop_fraction=0.5)
    assert t["stop"] == 100.0 and t["risk_points"] == 1.75


def test_entry_opening_beyond_the_stop_is_skipped_and_reported(cfg, orb):
    t, skipped = one(cfg, orb, dict(b16=dict(open=98.5)), cost_scale=0.0)    # gaps below the 99 stop
    assert t is None and len(skipped) == 1 and skipped["reason"].iloc[0] == "risk_below_minimum"


# ---------------------------------------------------------------- costs, to the dollar
def test_costs_one_tick_slippage_two_dollars_commission(cfg, orb):
    """entry fill 101.75+0.25 = 102.0, stop 99 -> R 3.0, target 105.0 (limit: no slippage)."""
    t, _ = one(cfg, orb, dict(b20=dict(high=105.0)))
    assert (t["entry_fill"], t["risk_points"], t["target"]) == (102.0, 3.0, 105.0)
    assert t["exit_fill"] == 105.0 and t["price_points"] == 3.0
    assert t["pnl_usd_nq"] == pytest.approx(3.0 * 20 - 4.0)                   # $2 per side, 2 sides
    assert t["pnl_usd_mnq"] == pytest.approx(3.0 * 2 - 1.0)                   # $0.50 per side
    assert t["r_multiple"] == pytest.approx((56.0 / 20) / 3.0)
    assert t["slippage_points"] == pytest.approx(0.25)                        # only the entry slipped


def test_stop_exit_pays_slippage_too(cfg, orb):
    t, _ = one(cfg, orb, dict(b20=dict(low=98.5)))
    assert t["exit_fill"] == 98.75 and t["price_points"] == pytest.approx(-3.25)
    assert t["pnl_usd_nq"] == pytest.approx(-3.25 * 20 - 4.0)
    assert t["slippage_points"] == pytest.approx(0.5)                         # entry + exit


def test_time_exit_pays_slippage(cfg, orb):
    t, _ = one(cfg, orb, dict(b385=dict(open=102.0)))
    assert t["exit_fill"] == 101.75 and t["price_points"] == pytest.approx(-0.25)


def test_zero_cost_scale_is_frictionless_and_double_scale_doubles_costs(cfg, orb):
    ov = dict(b20=dict(low=98.5))
    t0, _ = one(cfg, orb, ov, cost_scale=0.0)
    t1, _ = one(cfg, orb, ov, cost_scale=1.0)
    t2, _ = one(cfg, orb, ov, cost_scale=2.0)
    assert t0["slippage_points"] == 0 and t0["commission_usd_nq"] == 0
    assert t2["commission_usd_nq"] == 2 * t1["commission_usd_nq"] == 8.0
    assert t2["entry_fill"] - 101.75 == pytest.approx(0.5)                    # 2 ticks


def test_short_slippage_is_adverse_in_the_other_direction(cfg, orb):
    t, _ = one(cfg, orb, dict(SHORT_SIGNAL, b20=dict(high=101.5)))
    assert t["entry_fill"] == 98.0                                            # sold 1 tick BELOW the open
    assert t["exit_fill"] == 101.25                                           # bought back 1 tick ABOVE the stop


def test_params_validation(cfg):
    for bad in (dict(target_fill="maybe"), dict(ambiguity="coin_flip"), dict(target_r=-1.0), dict(cost_scale=-1.0)):
        with pytest.raises(ValueError):
            exec_params_from_cfg(cfg, **bad)


# ---------------------------------------------------------------- equivalence with the bar-by-bar twin
def tick_aligned_days(n, seed, range_minutes=15):
    """Random days made nastier than a plain random walk: ~3% of bars open with a gap and ~3% are
    very wide, so stop-and-target-in-one-bar and gap-through-stop paths actually occur."""
    b = random_days(n, seed=seed, range_minutes=range_minutes)
    rng = np.random.default_rng(seed + 1000)
    k = len(b)
    gap = rng.random(k) < 0.03
    b.loc[gap, "open"] = b.loc[gap, "open"] + rng.normal(0, 15, int(gap.sum()))
    wide = rng.random(k) < 0.03
    b.loc[wide, "high"] = b.loc[wide, "high"] + rng.uniform(5, 40, int(wide.sum()))
    b.loc[wide, "low"] = b.loc[wide, "low"] - rng.uniform(5, 40, int(wide.sum()))
    b["high"] = b[["high", "open", "close"]].max(axis=1)
    b["low"] = b[["low", "open", "close"]].min(axis=1)
    for c in ("open", "high", "low", "close"):
        b[c] = (b[c] / 0.25).round() * 0.25
    return b


GRID = [
    dict(),                                                                   # primary spec
    dict(target_r=1.5, slippage_ticks=2.0),
    dict(target_r=2.0, target_fill="through"),
    dict(target_r=None, slippage_ticks=3.0),
    dict(stop_fraction=0.5, target_r=1.0),
    dict(ambiguity="target_first", target_r=1.0, slippage_ticks=0.0),
    dict(cost_scale=0.0, target_r=1.5, stop_fraction=0.75),
]


@pytest.mark.parametrize("overrides", GRID, ids=lambda o: ",".join(f"{k}={v}" for k, v in o.items()) or "primary")
def test_vectorised_equals_event_driven(cfg, orb, overrides):
    bars = tick_aligned_days(450, seed=21)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    sig = generate_signals(bars, dates, orb)
    ex = exec_params_from_cfg(cfg, **overrides)
    v, _ = simulate(bars, sig, orb, ex)
    e = simulate_event_driven(bars, sig, orb, ex)
    assert list(v.index) == list(e.index) and len(v) > 100
    assert (v["exit_reason"].to_numpy() == e["exit_reason"].to_numpy()).all()
    assert (v["ambiguous_bar"].to_numpy() == e["ambiguous_bar"].to_numpy()).all()
    assert ((v["bars_held"] + sig.loc[v.index, "entry_idx"]).to_numpy() == e["exit_idx"].to_numpy()).all()
    for a, b in (("entry_fill", "entry_fill"), ("stop", "stop"), ("target", "target"), ("risk_points", "risk_points"),
                 ("exit_fill", "exit_fill"), ("price_points", "price_points"), ("pnl_usd_nq", "pnl_usd_nq")):
        np.testing.assert_allclose(v[a].to_numpy(), e[b].to_numpy(), rtol=0, atol=1e-9, equal_nan=True, err_msg=a)
    assert len(set(v["exit_reason"])) >= 2                                    # more than one exit type exercised


def test_random_days_exercise_every_exit_path(cfg, orb):
    bars = tick_aligned_days(500, seed=5)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    v, _ = simulate(bars, generate_signals(bars, dates, orb), orb, exec_params_from_cfg(cfg, target_r=1.0))
    assert {"stop", "stop_gap", "target", "time"} <= set(v["exit_reason"]) and v["ambiguous_bar"].any()


def test_accounting_identities_hold_on_random_days(cfg, orb):
    bars = tick_aligned_days(200, seed=9)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    sig = generate_signals(bars, dates, orb)
    for scale in (0.0, 1.0, 2.0):
        ex = exec_params_from_cfg(cfg, cost_scale=scale)
        v, _ = simulate(bars, sig, orb, ex)
        np.testing.assert_allclose(v["gross_points"] - v["slippage_points"], v["price_points"], atol=1e-9)
        np.testing.assert_allclose(v["pnl_usd_nq"], v["price_points"] * 20 - v["commission_usd_nq"], atol=1e-9)
        np.testing.assert_allclose(v["pnl_usd_mnq"], v["price_points"] * 2 - v["commission_usd_mnq"], atol=1e-9)
        assert (v["slippage_points"] >= -1e-12).all()                         # slippage never helps (limit slip is 0)
        if scale == 0.0:
            assert (v["slippage_points"] == 0).all() and (v["commission_usd_nq"] == 0).all()


# ---------------------------------------------------------------- no lookahead in the exit logic
def test_trade_ignores_bars_after_its_exit(cfg, orb):
    bars = tick_aligned_days(400, seed=13)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    sig = generate_signals(bars, dates, orb)
    ex = exec_params_from_cfg(cfg)
    base, _ = simulate(bars, sig, orb, ex)
    rng = np.random.default_rng(3)
    cols = ["exit_reason", "exit_fill", "pnl_usd_nq", "risk_points"]
    checked = 0
    for d, r in base.iterrows():
        exit_mod = r["exit_time"].hour * 60 + r["exit_time"].minute
        if exit_mod >= orb.flat_min:       # time exits use the last bar: nothing after it to scramble
            continue
        b = bars.copy()
        sel = (b["date"] == d) & (b["mod"] > exit_mod)
        n = int(sel.sum())
        px = 15000 + rng.normal(0, 500, n)
        b.loc[sel, "open"], b.loc[sel, "close"] = px, px
        b.loc[sel, "high"], b.loc[sel, "low"] = px + 40, px - 40
        again, _ = simulate(b, sig, orb, ex)
        assert (again.loc[d, cols] == r[cols]).all(), d
        checked += 1
    assert checked > 60


def test_perturbation_teeth_a_bar_before_exit_does_change_the_trade(cfg, orb):
    day = quiet_day("2023-06-05", **dict(LONG_SIGNAL, b20=dict(high=105.0)))
    ex = exec_params_from_cfg(cfg, cost_scale=0.0)
    base, _ = trade(cfg, orb, day, cost_scale=0.0)
    day2 = quiet_day("2023-06-05", **dict(LONG_SIGNAL, b20=dict(high=105.0), b18=dict(low=98.0)))   # earlier stop
    moved, _ = trade(cfg, orb, day2, cost_scale=0.0)
    assert base.iloc[0]["exit_reason"] == "target" and moved.iloc[0]["exit_reason"] == "stop"
