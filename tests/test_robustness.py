"""Stage 7 statistics, each checked against a known answer or a property that must hold."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from orb.analysis.metrics import drawdown_stats
from orb.analysis.robustness import (
    assign_regime, bootstrap_stat, daily_returns, deflated_sharpe, direction_permutation_test, expected_max_sharpe,
    freeze_terciles, max_drawdown_rows, mean_rows, percentile_ci, permuted_max_drawdowns, prior_volatility,
    probabilistic_sharpe, profit_factor_rows, reality_check, sharpe, sortino, stationary_bootstrap_indices, win_rate_rows,
)


# ---------------------------------------------------------------- bootstrap mechanics
def test_stationary_bootstrap_shape_bounds_and_reproducibility():
    a = stationary_bootstrap_indices(200, 50, 10, np.random.default_rng(1))
    b = stationary_bootstrap_indices(200, 50, 10, np.random.default_rng(1))
    assert a.shape == (50, 200) and a.min() >= 0 and a.max() < 200
    assert (a == b).all()                                                       # same seed, same resamples


@pytest.mark.parametrize("mean_block", [1, 5, 10, 25])
def test_block_length_matches_the_requested_mean(mean_block):
    n = 4000
    idx = stationary_bootstrap_indices(n, 20, mean_block, np.random.default_rng(0))
    continues = (idx[:, 1:] == (idx[:, :-1] + 1) % n).mean()                    # a step that stays inside a block
    expected = 1 - 1 / mean_block + 1 / (mean_block * n)
    assert continues == pytest.approx(expected, abs=0.02)


def test_bootstrap_ci_covers_the_truth_and_has_the_right_width_for_iid_data():
    rng = np.random.default_rng(3)
    x = rng.normal(0.0, 1.0, 800)
    dist = bootstrap_stat(x, mean_rows, 3000, 1, np.random.default_rng(4))
    lo, hi = percentile_ci(dist)
    assert lo < 0.0 < hi
    assert (hi - lo) == pytest.approx(2 * 1.96 * x.std(ddof=1) / np.sqrt(len(x)), rel=0.15)


def test_block_bootstrap_widens_the_interval_for_autocorrelated_data():
    rng = np.random.default_rng(5)
    e, x = rng.normal(size=3000), np.zeros(3000)
    for t in range(1, 3000):
        x[t] = 0.8 * x[t - 1] + e[t]                                            # strong clustering, like regimes
    w = {}
    for L in (1, 20):
        lo, hi = percentile_ci(bootstrap_stat(x, mean_rows, 1500, L, np.random.default_rng(6)))
        w[L] = hi - lo
    assert w[20] > 2.0 * w[1]                                                   # iid resampling understates the uncertainty


# ---------------------------------------------------------------- statistics with known values
def test_sharpe_and_sortino_known_values():
    assert sharpe(np.array([1.0, -1.0] * 50)) == pytest.approx(0.0, abs=1e-12)
    x = np.array([2.0, 0.0, 2.0, 0.0])
    assert sharpe(x, periods=1) == pytest.approx(1.0 / x.std(ddof=1))
    y = np.array([3.0, -1.0, 3.0, -1.0])
    assert sortino(y, periods=1) == pytest.approx(1.0 / np.sqrt(np.mean([0, 1, 0, 1])))
    assert sortino(np.array([1.0, 2.0])) == np.inf                              # no losing period


def test_win_rate_profit_factor_rows():
    x = np.array([[2.0, -1.0, 3.0, -4.0]])
    assert win_rate_rows(x)[0] == 0.5 and profit_factor_rows(x)[0] == pytest.approx(5.0 / 5.0)


def test_max_drawdown_rows_matches_the_stage5_helper_and_known_cases():
    assert max_drawdown_rows(np.array([[5, -3, -4, 2, 10, -1.0]]))[0] == 7.0
    assert max_drawdown_rows(np.array([[-1.0, -1.0, -1.0]]))[0] == 3.0          # account starts at 0
    rng = np.random.default_rng(2)
    x = rng.normal(0, 1, 500)
    ref = drawdown_stats(pd.Series(x, index=pd.bdate_range("2020-01-01", periods=500)))["max_drawdown"]
    assert max_drawdown_rows(x[None, :])[0] == pytest.approx(ref)


def test_permuted_drawdowns_properties():
    wins = np.ones(30)
    assert (permuted_max_drawdowns(wins, 50, np.random.default_rng(1)) == 0).all()      # nothing to draw down
    worst_first = np.r_[-np.ones(10), np.ones(10)]
    d = permuted_max_drawdowns(worst_first, 300, np.random.default_rng(1))
    assert d.max() <= 10 and max_drawdown_rows(worst_first[None, :])[0] == 10            # ordered losses = the worst possible path


# ---------------------------------------------------------------- multiple testing
def test_expected_max_sharpe_matches_the_published_scale_and_grows_with_trials():
    assert expected_max_sharpe(1, 1.0) == 0.0
    assert expected_max_sharpe(100, 1.0) == pytest.approx(2.53, abs=0.03)                # Bailey and Lopez de Prado, N = 100, unit variance
    assert expected_max_sharpe(10, 1.0) < expected_max_sharpe(100, 1.0) < expected_max_sharpe(1000, 1.0)


def test_psr_formula_for_normal_returns():
    sr, n = 0.1, 500
    assert probabilistic_sharpe(sr, 0.0, n, 0.0, 3.0) == pytest.approx(stats.norm.cdf(sr * np.sqrt(n - 1) / np.sqrt(1 + 0.5 * sr ** 2)))


def test_deflated_sharpe_properties():
    rng = np.random.default_rng(8)
    r = rng.normal(0.0006, 0.01, 2500)                                          # a modest real edge
    one = deflated_sharpe(r, n_trials=1, sharpe_variance=0.0004)
    assert one["dsr"] == pytest.approx(one["psr_vs_zero"])                      # one trial: no deflation
    d10, d500 = (deflated_sharpe(r, n, 0.0004)["dsr"] for n in (10, 500))
    assert one["dsr"] > d10 > d500                                              # more trials, less credit
    strong = deflated_sharpe(rng.normal(0.003, 0.01, 2500), 500, 0.0004)["dsr"]
    assert strong > d500                                                        # a bigger Sharpe survives more deflation
    noise = deflated_sharpe(rng.normal(0.0, 0.01, 2500), 96, 0.0004)["dsr"]
    assert noise < 0.95                                                         # skill-less returns are not "significant"


def test_reality_check_rejects_a_real_signal_but_not_the_best_of_many_noise_series():
    T, K = 1500, 40
    noise_p = []
    for seed in range(4):
        rng = np.random.default_rng(100 + seed)
        noise_p.append(reality_check(rng.normal(0, 1, (T, K)), 300, 10, np.random.default_rng(seed))["p_value"])
    assert np.mean(np.array(noise_p) > 0.05) >= 0.75                            # pure noise: the best of 40 is usually not "significant"
    rng = np.random.default_rng(7)
    data = rng.normal(0, 1, (T, K))
    data[:, 5] += 0.2                                                           # one genuinely profitable column
    res = reality_check(data, 300, 10, np.random.default_rng(1))
    assert res["p_value"] < 0.02 and res["best_column"] == 5


# ---------------------------------------------------------------- direction permutation test
def test_direction_test_detects_information_but_not_drift():
    rng = np.random.default_rng(11)
    n = 1500
    drift = rng.normal(6.0, 40.0, n)                                           # market drifts up 6 bps a day; no relation to direction
    random_dir = rng.choice([1, -1], n)
    r = direction_permutation_test(drift, random_dir, 2000, np.random.default_rng(1))
    assert r["p_value"] > 0.05 and r["always_long_mean_bps"] == pytest.approx(drift.mean())   # drift alone is NOT "information"
    informative = np.where(rng.random(n) < 0.6, np.sign(drift), -np.sign(drift))
    informative[informative == 0] = 1
    assert direction_permutation_test(drift, informative, 2000, np.random.default_rng(1))["p_value"] < 0.01


# ---------------------------------------------------------------- volatility regime
def _bars(days=40, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.bdate_range("2022-01-03", periods=days):
        ts = pd.date_range(f"{d.date()} 14:30", periods=20, freq="min", tz="UTC")
        base = 15000 + rng.normal(0, 20)
        rows.append(pd.DataFrame({"date": d, "ts_event": ts, "high": base + rng.uniform(5, 40), "low": base - rng.uniform(5, 40), "close": base}))
    return pd.concat(rows, ignore_index=True)


def test_prior_volatility_uses_only_earlier_days():
    bars = _bars()
    base = prior_volatility(bars, 20)
    dates = sorted(bars["date"].unique())
    assert base.iloc[:20].isna().all() and base.iloc[20:].notna().all()          # needs a full window of earlier days
    d = dates[25]
    b = bars.copy()
    b.loc[b["date"] == d, ["high", "low"]] = [99999.0, 1.0]                      # wreck the trade date itself
    assert prior_volatility(b, 20).loc[d] == pytest.approx(base.loc[d])           # ... today does not feed today's measure
    assert prior_volatility(b, 20).loc[dates[26]] != pytest.approx(base.loc[dates[26]])   # it feeds the NEXT day
    b2 = bars.copy()
    b2.loc[b2["date"] == dates[10], ["high", "low"]] = [99999.0, 1.0]
    assert prior_volatility(b2, 20).loc[d] != pytest.approx(base.loc[d])          # an earlier day inside the window does matter


def test_terciles_are_frozen_and_applied_to_new_data_unchanged():
    dev = pd.Series(np.linspace(0.005, 0.03, 300))
    thr = freeze_terciles(dev, [1 / 3, 2 / 3])
    assert thr[0] < thr[1]
    new = pd.Series([0.0001, np.nan, 0.5])                                       # a future calm day, a missing window, a wild day
    reg = assign_regime(new, thr)
    assert reg.iloc[0] == "low" and reg.iloc[2] == "high" and reg.iloc[1] is None
    assert freeze_terciles(dev, [1 / 3, 2 / 3]) == thr                           # re-freezing on the same dev data cannot drift
    counts = assign_regime(dev, thr).value_counts()
    assert counts.min() >= 99 and counts.max() <= 101                            # roughly thirds on the development data


# ---------------------------------------------------------------- daily returns
def test_daily_returns_zero_on_days_without_a_trade():
    trades = pd.DataFrame({"pnl_usd_nq": [100.0, -50.0], "entry_fill": [10000.0, 20000.0]}, index=pd.to_datetime(["2020-01-02", "2020-01-06"]))
    dates = pd.bdate_range("2020-01-01", "2020-01-07")
    r = daily_returns(trades, dates, 20.0)
    assert r.loc["2020-01-02"] == pytest.approx(100.0 / (10000.0 * 20)) and r.loc["2020-01-06"] == pytest.approx(-50.0 / (20000.0 * 20))
    assert r.loc["2020-01-03"] == 0.0 and len(r) == len(dates)


def test_frozen_volatility_thresholds_file_is_sane_and_from_development_data(cfg):
    import json
    from orb.config import resolve
    f = resolve(cfg, "logs/frozen_vol_thresholds.json")
    if not f.exists():
        pytest.skip("run `python -m orb robustness` first")
    t = json.loads(f.read_text())
    assert 0 < t["low_mid_threshold"] < t["mid_high_threshold"] < 0.2
    assert "development" in t["computed_on"] and "2023" in t["computed_on"] and "2024" not in t["computed_on"]
