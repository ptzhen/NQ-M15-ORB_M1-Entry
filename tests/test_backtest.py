"""Backtest layer: metrics with known answers, regime bookkeeping, trade-log invariants, holdout guard."""

import copy

import numpy as np
import pandas as pd
import pytest

from orb.analysis.backtest import export_trade_log, regime_of, run_backtest
from orb.analysis.metrics import drawdown_stats, group_stats, summarize
from orb.config import resolve
from orb.data.splits import HoldoutLockedError


# ---------------------------------------------------------------- metrics with known answers
D = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-10", "2020-01-20", "2020-02-01"])


@pytest.mark.parametrize(
    "pnl, n, max_dd, longest",
    [
        ([5, -3, -4, 2, 10, -1.0], 6, 7.0, 19),   # peak 01-01, regained 01-20
        ([-1, -1, -1.0], 3, 3.0, 2),              # never gets above the starting 0: underwater from day one
        ([1, 1, 1.0], 3, 0.0, 0),                 # back-to-back highs are not a drawdown
        ([5, -1, 1, 1, 1, 1.0], 6, 1.0, 2),
        ([5, 1, 1, -1.0], 4, 1.0, 7),             # finishes under water: 01-03 peak to the 01-10 end
    ],
)
def test_drawdown_stats_known_cases(pnl, n, max_dd, longest):
    r = drawdown_stats(pd.Series(pnl, index=D[:n]))
    assert r["max_drawdown"] == max_dd and r["longest_underwater_days"] == longest


def _toy_trades():
    return pd.DataFrame(
        {"r_multiple": [1.0, -1.0, 1.0, 1.0], "pnl_usd_nq": [100.0, -100.0, 100.0, 100.0], "pnl_usd_mnq": [10.0, -10.0, 10.0, 10.0],
         "net_points_nq": [5.0, -5.0, 5.0, 5.0], "g": ["a", "a", "b", "b"], "exit_reason": ["target"] * 4,
         "direction": [1, -1, 1, 1], "gross_points": 0.0, "slippage_points": 0.0, "commission_usd_nq": 0.0, "commission_usd_mnq": 0.0}
    )


def test_group_stats_and_summarize_known_numbers():
    t = _toy_trades()
    g = group_stats(t, "g")
    assert g.loc["a", "trades"] == 2 and g.loc["a", "win_rate"] == 0.5 and g.loc["a", "expectancy_r"] == 0.0
    assert g.loc["b", "profit_factor"] == np.inf and g.loc["b", "total_usd_nq"] == 200.0
    s = summarize(t)
    assert s["win_rate"] == 0.75 and s["profit_factor"] == 300.0 / 100.0 and s["total_usd_mnq"] == 20.0


# ---------------------------------------------------------------- regimes
def test_every_date_is_in_exactly_one_regime(cfg):
    dates = pd.bdate_range("2010-01-01", "2026-12-31")
    reg = regime_of(dates, cfg)
    assert reg.notna().all() and set(reg) == {r["name"] for r in cfg.regimes}


def test_overlapping_or_gapped_regimes_are_rejected(cfg):
    dates = pd.bdate_range("2019-12-20", "2020-01-10")
    overlap = copy.deepcopy(cfg)
    overlap.regimes[1] = dict(overlap.regimes[1], start="2019-06-01")
    with pytest.raises(ValueError, match="exactly one regime"):
        regime_of(dates, overlap)
    gap = copy.deepcopy(cfg)
    gap.regimes[1] = dict(gap.regimes[1], start="2020-03-01")
    with pytest.raises(ValueError, match="exactly one regime"):
        regime_of(dates, gap)


# ---------------------------------------------------------------- guard integration
def test_backtest_cannot_see_the_holdout_without_unlock(cfg, monkeypatch):
    if not (resolve(cfg, cfg.paths.processed_dir) / "daily.parquet").exists():
        pytest.skip("run `python -m orb data` first")
    monkeypatch.delenv("ORB_UNLOCK_HOLDOUT", raising=False)
    for part in ("holdout", "all"):
        with pytest.raises(HoldoutLockedError):
            run_backtest(cfg, part, purpose="unit test: must be refused")


# ---------------------------------------------------------------- invariants of the real dev trade log
@pytest.fixture(scope="module")
def res(cfg):
    if not (resolve(cfg, cfg.paths.processed_dir) / "daily.parquet").exists():
        pytest.skip("run `python -m orb data` first")
    return run_backtest(cfg, "dev", purpose="unit test: trade-log invariants")


def test_one_trade_per_day_only_on_tradable_dev_days(cfg, res):
    t = res.trades
    assert t.index.is_unique and len(t) > 3000
    assert t.index.max() < pd.Timestamp(cfg.splits.holdout_start)
    assert res.daily.loc[t.index, "tradable"].all()


def test_every_signal_is_either_a_trade_or_a_reported_skip(res):
    n_signals = int((res.signals["direction"] != 0).sum())
    assert len(res.trades) + len(res.skipped) == n_signals


def test_trade_timing_is_sane(cfg, res):
    t = res.trades
    assert (t["entry_time"] > t["signal_time"]).all() and (t["exit_time"] >= t["entry_time"]).all()
    exit_clock = t["exit_time"].dt.hour * 60 + t["exit_time"].dt.minute
    assert (exit_clock <= 15 * 60 + 55).all()                                   # flat by 15:55
    assert (t["entry_time"].dt.hour * 60 + t["entry_time"].dt.minute >= 9 * 60 + 46).all()
    assert (t["weekday"].astype(str).to_numpy() == t.index.day_name().to_numpy()).all()    # labels behind the weekday table
    assert (t["year"].to_numpy() == t.index.year.to_numpy()).all()
    assert set(t["direction"]) == {-1, 1} and set(t["exit_reason"]) <= {"stop", "stop_gap", "target", "time"}


def test_pnl_accounting_ties_out(cfg, res):
    t, pv = res.trades, cfg.data.point_value_usd
    np.testing.assert_allclose(t["gross_points"], t["direction"] * (t["exit_raw"] - t["entry_raw"]), atol=1e-9)
    np.testing.assert_allclose(t["price_points"], t["direction"] * (t["exit_fill"] - t["entry_fill"]), atol=1e-9)
    np.testing.assert_allclose(t["pnl_usd_nq"], t["price_points"] * pv.NQ - t["commission_usd_nq"], atol=1e-9)
    np.testing.assert_allclose(t["r_multiple"], t["pnl_usd_nq"] / pv.NQ / t["risk_points"], atol=1e-9)
    assert (t["risk_points"] > 0).all()
    # stops never lose less than 1R beyond costs; targets never win more than ~1R
    stops = t[t["exit_reason"] == "stop"]
    assert (stops["r_multiple"] < -0.99).all()


def test_breakdown_tables_add_up_to_the_total(res):
    t = res.trades
    for by in ("year", "regime", "weekday", "side"):
        g = group_stats(t, by)
        assert g["trades"].sum() == len(t)
        assert g["total_usd_nq"].sum() == pytest.approx(t["pnl_usd_nq"].sum())


def test_trade_log_export_roundtrip(res, tmp_path):
    p = tmp_path / "log.csv"
    export_trade_log(res.trades, p)
    back = pd.read_csv(p, index_col="date")
    assert len(back) == len(res.trades)
    assert {"entry_time", "exit_time", "exit_reason", "r_multiple", "pnl_usd_nq", "pnl_usd_mnq", "regime"} <= set(back.columns)
    assert back["pnl_usd_nq"].sum() == pytest.approx(res.trades["pnl_usd_nq"].sum(), rel=1e-6)


def test_signal_parameters_flow_through_overrides(cfg):
    if not (resolve(cfg, cfg.paths.processed_dir) / "daily.parquet").exists():
        pytest.skip("run `python -m orb data` first")
    r30 = run_backtest(cfg, "dev", purpose="unit test: range override", orb_overrides={"range_minutes": 30})
    assert (r30.signals.loc[r30.signals["direction"] != 0, "signal_idx"] >= 30).all()
    rt = run_backtest(cfg, "dev", purpose="unit test: exec override", target_r=None)
    assert (rt.trades["exit_reason"] != "target").all()
