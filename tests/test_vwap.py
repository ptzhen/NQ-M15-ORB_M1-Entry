"""VWAP indicator: hand-computed values, every anchor boundary, an independent bar-by-bar reference, and no-lookahead."""

import numpy as np
import pandas as pd
import pytest

from orb.config import resolve
from orb.indicators.vwap import vwap


def frame(ts_utc, close, vol, **extra):
    """Bars where high = low = close, so hlc3 == close and hand arithmetic is easy."""
    c = np.asarray(close, float)
    d = {"ts_event": pd.to_datetime(ts_utc, utc=True), "open": c, "high": c, "low": c, "close": c, "volume": np.asarray(vol, float)}
    d.update(extra)
    return pd.DataFrame(d)


# ---------------------------------------------------------------- the formula, by hand
def test_vwap_and_stdev_hand_computed():
    f = frame(["2023-06-05 13:30", "2023-06-05 13:31"], [10, 20], [1, 3])
    r = vwap(f)
    assert r["vwap"].tolist() == pytest.approx([10.0, 17.5])
    sd = np.sqrt((1 * 100 + 3 * 400) / 4 - 17.5 ** 2)                                  # volume-weighted std of {10 (w=1), 20 (w=3)}
    assert sd == pytest.approx(4.330127)
    assert r["upper_1"].tolist() == pytest.approx([10.0, 17.5 + sd]) and r["lower_1"].tolist() == pytest.approx([10.0, 17.5 - sd])


def test_bands_use_their_multipliers_and_the_default_is_band_one_only():
    f = frame(["2023-06-05 13:30", "2023-06-05 13:31"], [10, 20], [1, 3])
    assert [c for c in vwap(f).columns if c.startswith("upper")] == ["upper_1"]
    r = vwap(f, bands={1: 1.0, 2: 2.0, 3: 3.0})
    sd = r["upper_1"].iloc[1] - r["vwap"].iloc[1]
    assert r["upper_2"].iloc[1] - r["vwap"].iloc[1] == pytest.approx(2 * sd) and r["lower_3"].iloc[1] == pytest.approx(17.5 - 3 * sd)


def test_percentage_mode_is_a_percent_of_vwap():
    f = frame(["2023-06-05 13:30", "2023-06-05 13:31"], [10, 20], [1, 3])
    r = vwap(f, band_mode="Percentage", bands={1: 1.0, 2: 2.5})
    assert r["upper_1"].iloc[1] == pytest.approx(17.5 * 1.01) and r["lower_1"].iloc[1] == pytest.approx(17.5 * 0.99)
    assert r["upper_2"].iloc[1] == pytest.approx(17.5 * (1 + 0.025))


def test_constant_price_has_zero_width_bands_and_bands_are_ordered():
    f = frame(pd.date_range("2023-06-05 13:30", periods=30, freq="min", tz="UTC"), np.full(30, 15000.25), np.arange(1, 31))
    r = vwap(f)
    assert (r["upper_1"] == r["vwap"]).all()
    rng = np.random.default_rng(0)
    g = frame(pd.date_range("2023-06-05 13:30", periods=200, freq="min", tz="UTC"), 100 + rng.normal(0, 1, 200).cumsum(), rng.integers(1, 50, 200))
    rr = vwap(g, bands={1: 1.0})
    assert (rr["upper_1"] >= rr["vwap"]).all() and (rr["vwap"] >= rr["lower_1"]).all()


def test_sources():
    f = pd.DataFrame({"ts_event": pd.to_datetime(["2023-06-05 13:30"], utc=True), "open": [10.0], "high": [14.0], "low": [8.0], "close": [11.0], "volume": [5.0]})
    expect = {"hlc3": (14 + 8 + 11) / 3, "close": 11.0, "open": 10.0, "high": 14.0, "low": 8.0, "hl2": 11.0, "ohlc4": 10.75, "hlcc4": (14 + 8 + 22) / 4}
    for k, v in expect.items():
        assert vwap(f, source=k)["vwap"].iloc[0] == pytest.approx(v), k
    assert vwap(f, source=pd.Series([42.0]))["vwap"].iloc[0] == 42.0
    with pytest.raises(ValueError):
        vwap(f, source="vwap")


# ---------------------------------------------------------------- anchors
def ids(f, anchor, **kw):
    return vwap(f, anchor=anchor, **kw)["period"].tolist()


def test_session_resets_at_1800_et_and_follows_the_wall_clock_across_dst():
    # 2023-06-05, EDT (UTC-4): 17:59 ET = 21:59 UTC, 18:00 ET = 22:00 UTC
    f = frame(["2023-06-05 13:30", "2023-06-05 21:59", "2023-06-05 22:00"], [1, 1, 1], [1, 1, 1])
    assert ids(f, "Session") == [1, 1, 2]
    # 2023-03-10 (Fri), EST (UTC-5): the same wall-clock boundary is an hour later in UTC (22:59 vs 23:00)
    g = frame(["2023-03-10 22:59", "2023-03-10 23:00"], [1, 1], [1, 1])
    assert ids(g, "Session") == [1, 2]
    # DST day itself, 2023-03-12 (US clocks go forward at 02:00): 17:59 EDT = 21:59 UTC, 18:00 EDT = 22:00 UTC
    h = frame(["2023-03-12 21:59", "2023-03-12 22:00"], [1, 1], [1, 1])
    assert ids(h, "Session") == [1, 2]


def test_sunday_evening_belongs_to_monday_session_and_week():
    # Sun 2023-06-04 18:00 EDT (22:00 UTC) opens Monday's trade date; Mon 09:30 ET is the same session
    f = frame(["2023-06-04 22:00", "2023-06-05 13:30"], [1, 1], [1, 1])
    assert ids(f, "Session") == [1, 1]
    # Friday 15:59 ET and the following Sunday 18:00 ET are different weeks; Monday and Friday of one week are the same week
    w = frame(["2023-06-05 13:30", "2023-06-09 19:59", "2023-06-11 22:00"], [1, 1, 1], [1, 1, 1])
    assert ids(w, "Week") == [1, 1, 2]


@pytest.mark.parametrize(
    "anchor, a, b, resets",
    [
        ("Month", "2023-04-28 15:00", "2023-05-01 15:00", True),
        ("Month", "2023-05-01 15:00", "2023-05-31 15:00", False),
        ("Quarter", "2023-03-31 15:00", "2023-04-03 15:00", True),
        ("Quarter", "2023-04-03 15:00", "2023-06-30 15:00", False),
        ("Year", "2022-12-30 15:00", "2023-01-03 15:00", True),
        ("Year", "2023-01-03 15:00", "2023-12-29 15:00", False),
        ("Decade", "2021-06-01 15:00", "2029-06-01 15:00", False),
        ("Decade", "2029-12-28 15:00", "2030-01-02 15:00", True),
        ("Decade", "2020-01-02 15:00", "2021-01-04 15:00", False),     # a new YEAR, but not a new decade
        ("Century", "2099-12-30 15:00", "2100-01-03 15:00", True),
        ("Century", "2029-12-28 15:00", "2030-01-02 15:00", False),
    ],
)
def test_anchor_boundaries(anchor, a, b, resets):
    f = frame([a, b], [1, 1], [1, 1])
    assert (ids(f, anchor) == [1, 2]) is resets


def test_first_bar_starts_a_period_and_vwap_accumulates_within_it():
    f = frame(["2023-06-05 13:30", "2023-06-06 13:30", "2023-06-06 13:31"], [10, 20, 40], [1, 1, 1])
    assert vwap(f, "Session")["vwap"].tolist() == pytest.approx([10, 20, 30])          # new session restarts the average
    assert vwap(f, "Month")["vwap"].tolist() == pytest.approx([10, 15, 70 / 3])        # month keeps accumulating


def test_a_contract_change_resets_vwap_even_inside_a_session():
    f = frame(["2023-06-05 13:30", "2023-06-05 13:31", "2023-06-05 13:32"], [10, 20, 40], [1, 1, 1], instrument_id=[1, 1, 2])
    assert vwap(f, "Session")["period"].tolist() == [1, 1, 2] and vwap(f, "Session")["vwap"].iloc[2] == 40.0


def test_unsupported_and_invalid_inputs_are_loud():
    f = frame(["2023-06-05 13:30"], [1], [1])
    for a in ("Earnings", "Dividends", "Splits"):
        with pytest.raises(NotImplementedError):
            vwap(f, anchor=a)
    with pytest.raises(ValueError):
        vwap(f, anchor="Fortnight")
    with pytest.raises(ValueError):
        vwap(f, band_mode="Fixed")
    with pytest.raises(ValueError, match="No volume"):
        vwap(frame(["2023-06-05 13:30", "2023-06-05 13:31"], [1, 2], [0, 0]))
    with pytest.raises(ValueError, match="timezone-aware"):
        vwap(pd.DataFrame({"ts_event": pd.to_datetime(["2023-06-05 13:30"]), "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}))
    with pytest.raises(ValueError, match="time order"):
        vwap(frame(["2023-06-05 13:31", "2023-06-05 13:30"], [1, 2], [1, 1]))


def test_nan_until_the_period_has_traded_volume():
    f = frame(["2023-06-05 13:30", "2023-06-05 13:31", "2023-06-05 13:32"], [10, 20, 30], [0, 2, 2])
    r = vwap(f)
    assert np.isnan(r["vwap"].iloc[0]) and r["vwap"].iloc[1] == 20.0 and r["vwap"].iloc[2] == 25.0


def test_offset_shifts_the_plot_like_pine():
    f = frame(pd.date_range("2023-06-05 13:30", periods=6, freq="min", tz="UTC"), [1, 2, 3, 4, 5, 6], [1] * 6)
    base, off = vwap(f), vwap(f, offset=2)
    assert off["vwap"].iloc[2:].tolist() == pytest.approx(base["vwap"].iloc[:-2].tolist()) and off["vwap"].iloc[:2].isna().all()


# ---------------------------------------------------------------- independent reference + no lookahead
def reference(f, anchor_key, mode, mults):
    """Bar-by-bar transcription of the Pine definition (slow, deliberately naive), with an accurate two-pass variance."""
    ts = pd.DatetimeIndex(f["ts_event"]).tz_convert("America/New_York")
    out, key_prev, s_hist, v_hist = [], None, [], []
    for t, h, l, c, v in zip(ts, f["high"], f["low"], f["close"], f["volume"]):
        wall = t.tz_localize(None) + pd.Timedelta(hours=6)                     # 18:00 ET starts the next trade date
        key = anchor_key(wall)
        if key != key_prev:
            s_hist, v_hist, key_prev = [], [], key
        s_hist.append((h + l + c) / 3)
        v_hist.append(v)
        s_arr, v_arr = np.array(s_hist), np.array(v_hist)
        w = (s_arr * v_arr).sum() / v_arr.sum()
        sd = np.sqrt((v_arr * (s_arr - w) ** 2).sum() / v_arr.sum())             # weighted std about the weighted mean
        basis = sd if mode == "Standard Deviation" else w * 0.01
        out.append([w] + [w + basis * m for m in mults] + [w - basis * m for m in mults])
    return np.array(out)


@pytest.mark.parametrize("anchor, key", [("Session", lambda w: w.date()), ("Month", lambda w: (w.year, w.month)), ("Year", lambda w: w.year)])
@pytest.mark.parametrize("mode", ["Standard Deviation", "Percentage"])
def test_matches_the_naive_reference_on_random_bars(anchor, key, mode):
    rng = np.random.default_rng(7)
    ts = pd.date_range("2023-11-27 00:00", "2024-02-02 23:59", freq="37min", tz="UTC")
    n = len(ts)
    close = 15000 + rng.normal(0, 30, n).cumsum()
    f = pd.DataFrame({"ts_event": ts, "high": close + rng.uniform(0, 8, n), "low": close - rng.uniform(0, 8, n), "close": close, "volume": rng.integers(1, 500, n).astype(float)})
    f["open"] = f["close"]
    mults = [1.0, 2.0, 3.0]
    got = vwap(f, anchor=anchor, band_mode=mode, bands=dict(zip([1, 2, 3], mults)))
    cols = ["vwap", "upper_1", "upper_2", "upper_3", "lower_1", "lower_2", "lower_3"]
    np.testing.assert_allclose(got[cols].to_numpy(), reference(f, key, mode, mults), rtol=1e-12, atol=1e-8)
    assert got["period"].nunique() >= 2                                          # the boundary logic was actually exercised


def test_no_lookahead_future_bars_cannot_change_earlier_values():
    rng = np.random.default_rng(3)
    ts = pd.date_range("2023-06-05 13:30", periods=300, freq="min", tz="UTC")
    c = 100 + rng.normal(0, 1, 300).cumsum()
    f = frame(ts, c, rng.integers(1, 100, 300))
    base = vwap(f, bands={1: 1.0, 2: 2.0})
    g = f.copy()
    wild = rng.normal(5000, 900, 150)                                                  # rewrite everything after bar 149
    for col in ("open", "high", "low", "close"):
        g.loc[150:, col] = wild
    g.loc[150:, "volume"] = 10 ** 6
    after = vwap(g, bands={1: 1.0, 2: 2.0})
    pd.testing.assert_frame_equal(base.iloc[:150], after.iloc[:150])
    assert not np.allclose(base["vwap"].iloc[160:], after["vwap"].iloc[160:])              # ... and the test can fail


# ---------------------------------------------------------------- real development data
def test_session_vwap_on_real_dev_days_matches_a_plain_numpy_calculation(cfg):
    if not (resolve(cfg, cfg.paths.processed_dir) / "daily.parquet").exists():
        pytest.skip("run `python -m orb data` first")
    from orb.data.splits import load_bars_chosen
    bars = load_bars_chosen(cfg, "dev", purpose="unit test: VWAP on real dev days (no holdout)").sort_values("ts_event")
    sub = bars[bars["date"].isin(sorted(bars["date"].unique())[-6:])].reset_index(drop=True)
    r = vwap(sub, "Session")
    for d, g in sub.groupby("date"):
        s = (g["high"] + g["low"] + g["close"]) / 3
        last = r.loc[g.index[-1]]
        assert last["vwap"] == pytest.approx((s * g["volume"]).sum() / g["volume"].sum(), rel=1e-12)
    assert r["period"].nunique() == 6 and r["vwap"].notna().all()


def test_single_bar_periods_have_exactly_zero_width_at_nq_price_levels():
    """Regression for catastrophic cancellation: one bar in a period means zero variance, even at 15,000-point prices."""
    f = frame(["2023-06-05 13:30", "2023-06-06 13:30", "2023-06-07 13:30"], [15234.25, 15310.75, 15199.5], [900, 1200, 700])
    r = vwap(f)
    assert (r["upper_1"] == r["vwap"]).all() and (r["lower_1"] == r["vwap"]).all()
