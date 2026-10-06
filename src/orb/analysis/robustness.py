"""Stage 7 statistics: bootstrap, trade-order Monte Carlo, volatility regimes, direction test, multiple testing.

Everything here is a pure function of arrays/frames so it can be unit-tested against known answers.
Protocol (resample counts, block lengths, definitions, thresholds) was fixed before running: docs/DECISIONS.md D6.

A note on why the plain bootstrap is not enough: outcomes cluster in time (a bad regime produces many
bad trades in a row), so resampling single trades as if they were independent makes intervals too narrow.
The *stationary bootstrap* resamples random-length blocks of consecutive observations instead.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from scipy import stats

TRADING_DAYS = 252


# --------------------------------------------------------------------------------------
# Resampling
# --------------------------------------------------------------------------------------
def stationary_bootstrap_indices(n: int, resamples: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano stationary bootstrap: (resamples, n) index array. At every step, with probability 1/mean_block a new
    block starts at a uniformly random position; otherwise the index advances by one (wrapping). Block lengths are
    geometric with mean ``mean_block``; ``mean_block = 1`` is the ordinary i.i.d. bootstrap."""
    p = 1.0 / mean_block
    new_block = rng.random((resamples, n)) < p
    starts = rng.integers(0, n, size=(resamples, n))
    idx = np.empty((resamples, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for t in range(1, n):
        idx[:, t] = np.where(new_block[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def bootstrap_stat(values: np.ndarray, stat: Callable[[np.ndarray], np.ndarray], resamples: int, mean_block: float, rng: np.random.Generator,
                   chunk: int = 2000) -> np.ndarray:
    """Distribution of ``stat`` over bootstrap resamples. ``stat`` maps a (chunk, n) array to a (chunk,) array."""
    n = len(values)
    out = []
    for lo in range(0, resamples, chunk):
        k = min(chunk, resamples - lo)
        idx = stationary_bootstrap_indices(n, k, mean_block, rng)
        out.append(stat(values[idx]))
    return np.concatenate(out)


def percentile_ci(dist: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    a = (1 - level) / 2
    return float(np.quantile(dist, a)), float(np.quantile(dist, 1 - a))


# --------------------------------------------------------------------------------------
# Performance statistics on resampled rows (axis=1 is time)
# --------------------------------------------------------------------------------------
def mean_rows(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1)


def win_rate_rows(x: np.ndarray) -> np.ndarray:
    return (x > 0).mean(axis=1)


def profit_factor_rows(x: np.ndarray) -> np.ndarray:
    gains, losses = np.where(x > 0, x, 0).sum(axis=1), -np.where(x < 0, x, 0).sum(axis=1)
    return np.where(losses > 0, gains / np.where(losses > 0, losses, 1), np.inf)


def sharpe_rows(x: np.ndarray, periods: int = TRADING_DAYS) -> np.ndarray:
    sd = x.std(axis=1, ddof=1)
    return np.where(sd > 0, x.mean(axis=1) / np.where(sd > 0, sd, 1) * np.sqrt(periods), 0.0)


def sortino_rows(x: np.ndarray, periods: int = TRADING_DAYS) -> np.ndarray:
    downside = np.sqrt(np.mean(np.minimum(x, 0.0) ** 2, axis=1))
    mean = x.mean(axis=1)
    no_losses = np.where(mean > 0, np.inf, 0.0)               # nothing below zero: infinite if profitable, else undefined -> 0
    return np.where(downside > 0, mean / np.where(downside > 0, downside, 1) * np.sqrt(periods), no_losses)


def sharpe(x: np.ndarray, periods: int = TRADING_DAYS) -> float:
    return float(sharpe_rows(np.asarray(x)[None, :], periods)[0])


def sortino(x: np.ndarray, periods: int = TRADING_DAYS) -> float:
    return float(sortino_rows(np.asarray(x)[None, :], periods)[0])


def max_drawdown_rows(pnl: np.ndarray) -> np.ndarray:
    """Max peak-to-trough fall of the cumulative sum along axis 1, with the account starting at 0."""
    eq = np.cumsum(pnl, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((eq.shape[0], 1)), eq], axis=1), axis=1)[:, 1:]
    return (peak - eq).max(axis=1)


def permuted_max_drawdowns(pnl: np.ndarray, permutations: int, rng: np.random.Generator, chunk: int = 1000) -> np.ndarray:
    """Max drawdown for random re-orderings of the same trades. Same total P&L, different path."""
    out = []
    for lo in range(0, permutations, chunk):
        k = min(chunk, permutations - lo)
        rows = np.stack([rng.permutation(pnl) for _ in range(k)])
        out.append(max_drawdown_rows(rows))
    return np.concatenate(out)


# --------------------------------------------------------------------------------------
# Multiple testing
# --------------------------------------------------------------------------------------
def probabilistic_sharpe(sr: float, sr_benchmark: float, n_obs: int, skew: float, kurt: float) -> float:
    """PSR: probability the true Sharpe exceeds ``sr_benchmark``. Sharpe in per-period units. ``kurt`` is the plain (non-excess) kurtosis."""
    denom = np.sqrt(max(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2, 1e-12))
    return float(stats.norm.cdf((sr - sr_benchmark) * np.sqrt(n_obs - 1) / denom))


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """Expected maximum Sharpe among ``n_trials`` skill-less strategies (Bailey and Lopez de Prado, 2014). Per-period units."""
    if n_trials <= 1:
        return 0.0
    g = np.euler_gamma
    return float(np.sqrt(sharpe_variance) * ((1 - g) * stats.norm.ppf(1 - 1.0 / n_trials) + g * stats.norm.ppf(1 - 1.0 / (n_trials * np.e))))


def deflated_sharpe(returns: np.ndarray, n_trials: int, sharpe_variance: float) -> dict:
    """Deflated Sharpe ratio of one return series given how many strategies were tried. All Sharpes per period."""
    r = np.asarray(returns, dtype=float)
    sr = r.mean() / r.std(ddof=1)
    sr0 = expected_max_sharpe(n_trials, sharpe_variance)
    skew, kurt = float(stats.skew(r)), float(stats.kurtosis(r, fisher=False))
    return {"sharpe_daily": float(sr), "sharpe_annual": float(sr * np.sqrt(TRADING_DAYS)), "benchmark_daily": sr0,
            "benchmark_annual": sr0 * np.sqrt(TRADING_DAYS), "dsr": probabilistic_sharpe(sr, sr0, len(r), skew, kurt), "psr_vs_zero": probabilistic_sharpe(sr, 0.0, len(r), skew, kurt)}


def reality_check(returns: np.ndarray, resamples: int, mean_block: float, rng: np.random.Generator) -> dict:
    """White's Reality Check. ``returns`` is (T, K): daily returns of K candidate strategies. H0: no strategy has positive
    expected return. Statistic: max_k sqrt(T) * mean_k. The null distribution comes from a stationary bootstrap of the
    column-centred returns (resampling whole days so cross-strategy dependence is kept)."""
    T, K = returns.shape
    mu = returns.mean(axis=0)
    observed = float(np.sqrt(T) * mu.max())
    centred = returns - mu
    maxima = np.empty(resamples)
    for lo in range(0, resamples, 200):
        k = min(200, resamples - lo)
        idx = stationary_bootstrap_indices(T, k, mean_block, rng)
        for j in range(k):
            maxima[lo + j] = np.sqrt(T) * centred[idx[j]].mean(axis=0).max()
    return {"observed": observed, "p_value": float((maxima >= observed).mean()), "null_maxima": maxima, "best_column": int(mu.argmax())}


# --------------------------------------------------------------------------------------
# Is the breakout direction informative? (hold-to-close permutation test)
# --------------------------------------------------------------------------------------
def direction_permutation_test(price_move_bps: np.ndarray, direction: np.ndarray, permutations: int, rng: np.random.Generator) -> dict:
    """``price_move_bps``: signed-by-market return (not by direction) from entry to the time exit, in bps of price, one per signal day.
    Statistic: mean of direction * move. Null: direction labels carry no information, so reshuffle them across days
    (keeping the long/short counts). Market drift is shared by every shuffle, so it cannot create a false positive."""
    move, d = np.asarray(price_move_bps, float), np.asarray(direction, float)
    observed = float((d * move).mean())
    null = np.empty(permutations)
    for i in range(permutations):
        null[i] = (rng.permutation(d) * move).mean()
    return {"observed_mean_bps": observed, "null_mean_bps": float(null.mean()), "null_sd_bps": float(null.std()),
            "p_value": float((np.sum(null >= observed) + 1) / (permutations + 1)),
            "long_mean_bps": float(move[d > 0].mean()), "short_mean_bps": float(-move[d < 0].mean()), "always_long_mean_bps": float(move.mean())}


# --------------------------------------------------------------------------------------
# Volatility regime (known before the open)
# --------------------------------------------------------------------------------------
def prior_volatility(bars: pd.DataFrame, lookback: int) -> pd.Series:
    """For each date: mean of (RTH high - RTH low) / RTH close over the ``lookback`` trading days BEFORE that date.
    ``bars`` are RTH bars with ``date`` and ``mod``; days are those present in ``bars``. Today's bars are never used."""
    g = bars.sort_values("ts_event").groupby("date")
    day_range = (g["high"].max() - g["low"].min()) / g["close"].last()
    return day_range.rolling(lookback, min_periods=lookback).mean().shift(1)


def freeze_terciles(prior_vol_on_trade_days: pd.Series, quantiles) -> tuple[float, float]:
    v = prior_vol_on_trade_days.dropna()
    lo, hi = np.quantile(v, quantiles)
    return float(lo), float(hi)


def assign_regime(prior_vol: pd.Series, thresholds: tuple[float, float]) -> pd.Series:
    """'low' / 'mid' / 'high' using FROZEN thresholds; NaN where the trailing window is not yet available."""
    lo, hi = thresholds
    out = pd.Series(np.where(prior_vol.isna(), None, np.where(prior_vol <= lo, "low", np.where(prior_vol <= hi, "mid", "high"))), index=prior_vol.index, dtype="object")
    return out


# --------------------------------------------------------------------------------------
# Daily return series for risk statistics
# --------------------------------------------------------------------------------------
def daily_returns(trades: pd.DataFrame, all_dates: pd.DatetimeIndex, point_value: float) -> pd.Series:
    """Net return on notional per trading day (one contract's notional = fill price x multiplier); 0 on days without a trade."""
    r = trades["pnl_usd_nq"] / (trades["entry_fill"] * point_value)
    return r.reindex(all_dates).fillna(0.0)
