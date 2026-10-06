"""Signal-engine tests, with the emphasis on proving there is NO lookahead.

Three independent lines of evidence:

1. Hand-built days with a known right answer (timing, ties, cut-off, DST).
2. Equivalence: the vectorised engine must equal a bar-by-bar state machine that can only
   see the past, on hundreds of randomised days (and on real development data).
3. Perturbation: rewrite everything the signal is NOT allowed to depend on (the future,
   other days) with garbage; the signal must not move. Rewrite something it IS allowed to
   depend on and it must move. A test that can never fail proves nothing.
"""

import numpy as np
import pandas as pd
import pytest

from orb.config import hhmm_to_minutes, resolve
from orb.data.daily import add_et_columns
from orb.signals.orb import (
    LONG, NONE, SHORT, ORBParams, event_driven_signals, generate_signals, params_from_cfg,
)

TZ = "America/New_York"


@pytest.fixture
def params(cfg):
    return params_from_cfg(cfg)   # 15-minute range, open 09:30, flat 15:55, last entry bar 15:54


def mk_day(date, o, h, l, c, instrument_id=1):
    """Bars for one ET date from 386 per-minute arrays (09:30 ... 15:55 inclusive)."""
    ts = pd.date_range(f"{date} 09:30", f"{date} 15:55", freq="min", tz=TZ)
    assert len(ts) == len(o) == 386
    df = pd.DataFrame({"ts_event": ts.tz_convert("UTC"), "instrument_id": instrument_id, "symbol": "NQM3",
                       "open": o, "high": h, "low": l, "close": c, "volume": 100})
    return add_et_columns(df, TZ)


def quiet_day(date, range_hi=101.0, range_lo=99.0, level=100.0, **overrides):
    """Flat day at ``level``; the first 15 bars span [range_lo, range_hi]; later bars sit inside it.
    ``overrides`` maps bar index -> dict(open=, high=, low=, close=) to script specific bars."""
    o = np.full(386, level); h = np.full(386, level); l = np.full(386, level); c = np.full(386, level)
    h[:15], l[:15] = level, level
    h[3], l[7] = range_hi, range_lo
    for idx, v in overrides.items():
        i = int(idx.lstrip("b"))
        for k, x in v.items():
            {"open": o, "high": h, "low": l, "close": c}[k][i] = x
    return mk_day(date, o, h, l, c)


def run(bars, params):
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    return generate_signals(bars, dates, params), event_driven_signals(bars, dates, params)


# ---------------------------------------------------------------- 1. known answers
def test_no_breakout_means_no_signal(params):
    v, e = run(quiet_day("2023-06-05"), params)
    assert v["direction"].iloc[0] == NONE and e["direction"].iloc[0] == NONE


def test_long_breakout_timing_and_entry_price(params):
    day = quiet_day("2023-06-05", b15=dict(close=101.5, high=101.5), b16=dict(open=101.75))
    v, e = run(day, params)
    r = v.iloc[0]
    assert r["direction"] == LONG
    assert r["signal_idx"] == 15 and r["entry_idx"] == 16            # signal on the 09:45 bar, entry on the next
    assert r["signal_time"].strftime("%H:%M") == "09:45" and r["entry_time"].strftime("%H:%M") == "09:46"
    assert r["entry_open"] == 101.75                                  # the NEXT bar's open, not the signal close
    assert r["signal_close"] == 101.5
    assert (r["range_high"], r["range_low"]) == (101.0, 99.0)
    assert e.iloc[0]["entry_open"] == 101.75


def test_short_breakout(params):
    v, _ = run(quiet_day("2023-06-05", b30=dict(close=98.5, low=98.5)), params)
    assert v["direction"].iloc[0] == SHORT and v["signal_idx"].iloc[0] == 30


def test_close_equal_to_range_edge_is_not_a_breakout(params):
    v, e = run(quiet_day("2023-06-05", b20=dict(close=101.0), b25=dict(close=99.0)), params)
    assert v["direction"].iloc[0] == NONE == e["direction"].iloc[0]


def test_wick_beyond_range_without_close_beyond_is_not_a_breakout(params):
    v, _ = run(quiet_day("2023-06-05", b20=dict(high=105.0, low=95.0)), params)   # close stays at 100
    assert v["direction"].iloc[0] == NONE


def test_first_breakout_wins_and_only_one_trade_per_day(params):
    day = quiet_day("2023-06-05", b20=dict(close=101.5, high=101.5), b25=dict(close=98.0, low=98.0))
    v, e = run(day, params)
    assert v["direction"].iloc[0] == LONG and v["signal_idx"].iloc[0] == 20
    assert len(v) == 1


def test_entry_bar_must_exist_before_the_exit(params):
    # last_entry_bar = 15:54 (idx 384) -> latest signal bar is 15:53 (idx 383)
    ok, _ = run(quiet_day("2023-06-05", b383=dict(close=101.5, high=101.5)), params)
    assert ok["direction"].iloc[0] == LONG and ok["entry_idx"].iloc[0] == 384
    late, e = run(quiet_day("2023-06-05", b384=dict(close=101.5, high=101.5)), params)
    assert late["direction"].iloc[0] == NONE == e["direction"].iloc[0]


def test_range_is_only_the_first_n_bars(params):
    # A huge bar at idx 15 (the first post-range bar) must NOT widen the range.
    day = quiet_day("2023-06-05", b15=dict(high=500.0, low=1.0, close=101.5))
    v, _ = run(day, params)
    assert v["range_high"].iloc[0] == 101.0 and v["range_low"].iloc[0] == 99.0
    assert v["direction"].iloc[0] == LONG


def test_bar_inside_range_window_never_signals(params):
    # idx 14 (09:44) closes at the range high: inside the window, so it only builds the range
    day = quiet_day("2023-06-05", b14=dict(close=101.0, high=102.0))
    v, _ = run(day, params)
    assert v["range_high"].iloc[0] == 102.0 and v["direction"].iloc[0] == NONE


@pytest.mark.parametrize("n", [5, 30, 60])
def test_range_length_is_a_parameter(cfg, n):
    p = params_from_cfg(cfg, range_minutes=n)
    day = quiet_day("2023-06-05", b70=dict(close=101.5, high=101.5))
    v, e = run(day, p)
    assert v["signal_idx"].iloc[0] == 70 and e["signal_idx"].iloc[0] == 70


def test_dst_days_use_et_wall_clock_for_the_range(params):
    """Same ET clock times on either side of the March DST change, despite a 1h UTC shift."""
    d1, d2 = quiet_day("2023-03-10", b15=dict(close=101.5, high=101.5)), quiet_day("2023-03-13", b15=dict(close=101.5, high=101.5))
    v, _ = run(pd.concat([d1, d2], ignore_index=True), params)
    assert list(v["signal_time"].dt.strftime("%H:%M")) == ["09:45", "09:45"]
    utc = v["signal_time"].dt.tz_convert("UTC").dt.hour.tolist()
    assert utc == [14, 13]                                            # EST then EDT


def test_missing_bars_are_refused_not_silently_skipped(params):
    day = quiet_day("2023-06-05")
    day = day[day["mod"] != 12 * 60]
    with pytest.raises(ValueError, match="missing bars"):
        generate_signals(day, pd.DatetimeIndex([pd.Timestamp("2023-06-05")]), params)


def test_params_validation(cfg):
    with pytest.raises(ValueError):
        params_from_cfg(cfg, last_entry_bar="15:55")   # entry and exit would share a bar


# ---------------------------------------------------------------- 2. equivalence on random data
def random_days(n_days, seed, range_minutes=15):
    """Random-walk days (quiet and wild). ~35% are 'range-bound': every close after the range
    window is clipped inside the range, so the no-signal path is exercised too."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    frames = []
    for d in dates:
        vol = rng.choice([0.05, 0.25, 1.0])
        ret = rng.normal(0, vol, 386)
        c = 15000 + np.cumsum(ret)
        o = np.r_[15000.0, c[:-1]]
        h = np.maximum(o, c) + np.abs(rng.normal(0, vol / 2, 386))
        l = np.minimum(o, c) - np.abs(rng.normal(0, vol / 2, 386))
        if rng.random() < 0.35:
            lo, hi = l[:range_minutes].min(), h[:range_minutes].max()
            c[range_minutes:] = np.clip(c[range_minutes:], lo, hi)
            o = np.r_[15000.0, c[:-1]]
            h = np.maximum(h, np.maximum(o, c))
            l = np.minimum(l, np.minimum(o, c))
        frames.append(mk_day(d.strftime("%Y-%m-%d"), o, h, l, c))
    return pd.concat(frames, ignore_index=True)


@pytest.mark.parametrize("range_minutes", [5, 15, 30, 60])
def test_vectorised_equals_event_driven_on_random_days(cfg, range_minutes):
    p = params_from_cfg(cfg, range_minutes=range_minutes)
    bars = random_days(250, seed=range_minutes, range_minutes=range_minutes)
    v, e = run(bars, p)
    cols = ["direction", "range_high", "range_low", "signal_idx", "signal_close", "entry_idx", "entry_open"]
    pd.testing.assert_frame_equal(v[cols].astype(float), e[cols].astype(float), check_exact=True)
    assert (v["direction"] != 0).sum() > 50 and (v["direction"] == 0).sum() > 5   # both outcomes exercised


# ---------------------------------------------------------------- 3. perturbation: no lookahead
def _rewrite(bars, date, mask_fn, rng):
    """Replace OHLC of the selected bars on one date with large random garbage (valid OHLC)."""
    b = bars.copy()
    sel = (b["date"] == pd.Timestamp(date)) & mask_fn(b)
    n = int(sel.sum())
    px = 15000 + rng.normal(0, 400, n)
    b.loc[sel, "open"] = px
    b.loc[sel, "close"] = px + rng.normal(0, 50, n)
    hi = np.maximum(b.loc[sel, "open"], b.loc[sel, "close"]) + 30
    lo = np.minimum(b.loc[sel, "open"], b.loc[sel, "close"]) - 30
    b.loc[sel, "high"], b.loc[sel, "low"] = hi, lo
    return b


def test_signal_ignores_everything_after_the_entry_bars_open(params):
    """Garbage in every bar after the entry bar, AND in the entry bar's high/low/close (only its
    open is usable) must leave direction, range, signal bar and entry price untouched."""
    bars = random_days(120, seed=7)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    base = generate_signals(bars, dates, params)
    rng = np.random.default_rng(1)
    checked = 0
    for d, r in base[base["direction"] != 0].iterrows():
        entry_mod = params.open_min + int(r["entry_idx"])
        b = _rewrite(bars, d, lambda x: x["mod"] > entry_mod, rng)                  # all later bars
        sel = (b["date"] == d) & (b["mod"] == entry_mod)
        b.loc[sel, ["high", "low", "close"]] = [20000.0, 10000.0, 15000.0]          # entry bar except its open
        after = generate_signals(b, dates, params).loc[d]
        for col in ("direction", "range_high", "range_low", "signal_idx", "signal_close", "entry_idx", "entry_open"):
            assert after[col] == r[col], (d, col)
        checked += 1
    assert checked > 30


def test_range_ignores_bars_after_the_range_window(params):
    bars = random_days(40, seed=3)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    base = generate_signals(bars, dates, params)
    rng = np.random.default_rng(2)
    for d in dates[:20]:
        b = _rewrite(bars, d, lambda x: x["mod"] >= params.open_min + params.range_minutes, rng)
        after = generate_signals(b, dates, params).loc[d]
        assert after["range_high"] == base.loc[d, "range_high"] and after["range_low"] == base.loc[d, "range_low"]


def test_days_are_independent(params):
    bars = random_days(30, seed=11)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    base = generate_signals(bars, dates, params)
    b = _rewrite(bars, dates[10], lambda x: x["mod"] >= 0, np.random.default_rng(5))
    after = generate_signals(b, dates, params)
    other = dates.drop(dates[10])
    pd.testing.assert_frame_equal(base.loc[other, ["direction", "entry_open", "range_high"]],
                                  after.loc[other, ["direction", "entry_open", "range_high"]])


def test_perturbation_test_has_teeth(params):
    """Sanity check on the test itself: changing something the signal MAY depend on does move it."""
    bars = random_days(60, seed=7)
    dates = pd.DatetimeIndex(sorted(bars["date"].unique()))
    base = generate_signals(bars, dates, params)
    d = base[base["direction"] != 0].index[0]
    r = base.loc[d]
    # (a) the entry bar's open IS used
    sel = (bars["date"] == d) & (bars["mod"] == params.open_min + int(r["entry_idx"]))
    b = bars.copy(); b.loc[sel, "open"] += 5.0
    assert generate_signals(b, dates, params).loc[d, "entry_open"] == r["entry_open"] + 5.0
    # (b) the signal bar's close IS used: pull it back inside the range and the signal must go away/move
    sel = (bars["date"] == d) & (bars["mod"] == params.open_min + int(r["signal_idx"]))
    b = bars.copy(); b.loc[sel, "close"] = (r["range_high"] + r["range_low"]) / 2
    moved = generate_signals(b, dates, params).loc[d]
    assert (moved["signal_idx"], moved["direction"]) != (r["signal_idx"], r["direction"])


# ---------------------------------------------------------------- 4. real development data
def _dev_bars(cfg):
    from orb.data.splits import load_bars_chosen, load_daily
    if not (resolve(cfg, cfg.paths.processed_dir) / "daily.parquet").exists():
        pytest.skip("run `python -m orb data` first")
    daily = load_daily(cfg, "dev", purpose="unit test: signal equivalence on dev data")   # dev only: holdout stays locked
    bars = load_bars_chosen(cfg, "dev", purpose="unit test: signal equivalence on dev data")
    return bars, daily[daily["tradable"]].index


def test_real_dev_data_vectorised_equals_event_driven(cfg, params):
    bars, dates = _dev_bars(cfg)
    dates = dates[::5]   # every 5th tradable day keeps the slow reference fast
    v = generate_signals(bars, dates, params)
    e = event_driven_signals(bars, dates, params)
    cols = ["direction", "range_high", "range_low", "signal_idx", "signal_close", "entry_idx", "entry_open"]
    pd.testing.assert_frame_equal(v[cols].astype(float), e[cols].astype(float), check_exact=True)


def test_real_dev_data_invariants(cfg, params):
    bars, dates = _dev_bars(cfg)
    s = generate_signals(bars, dates, params)
    t = s[s["direction"] != 0]
    assert len(t) > 1000
    assert (t["signal_time"].dt.hour * 60 + t["signal_time"].dt.minute >= 9 * 60 + 45).all()    # never before the range completes
    assert ((t["entry_time"] - t["signal_time"]) == pd.Timedelta(minutes=1)).all()               # entry = next bar
    assert (t["entry_time"].dt.hour * 60 + t["entry_time"].dt.minute <= 15 * 60 + 54).all()
    assert (t["range_width"] > 0).all()
    long_ok = t.loc[t["direction"] == LONG, "signal_close"] > t.loc[t["direction"] == LONG, "range_high"]
    short_ok = t.loc[t["direction"] == SHORT, "signal_close"] < t.loc[t["direction"] == SHORT, "range_low"]
    assert long_ok.all() and short_ok.all()
