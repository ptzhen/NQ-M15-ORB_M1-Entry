"""Stage 6: parameter grid, smoothed selection, in/out-of-sample split and walk-forward.

The protocol (grid, selection rule, promotion criteria) was fixed and committed BEFORE any of this was run:
see ``docs/DECISIONS.md`` D4. The selection logic below is deliberately written as pure functions on plain
arrays/dicts so it can be unit-tested without market data.

Why smoothing: picking the single best of 96 cells picks the luckiest one. Averaging each cell with its grid
neighbours rewards *regions* where nearby parameter choices all work, which is what a real effect looks like
and a noise spike does not.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from orb.analysis.backtest import enrich
from orb.data.splits import load_bars_chosen, load_daily
from orb.execution.simulate import exec_params_from_cfg, simulate
from orb.signals.orb import generate_signals, params_from_cfg

Cell = tuple  # (range_minutes, stop_fraction, target_r or None)


@dataclass(frozen=True)
class Grid:
    ranges: tuple
    stops: tuple
    targets: tuple

    @property
    def shape(self) -> tuple[int, int, int]:
        return len(self.ranges), len(self.stops), len(self.targets)

    @property
    def cells(self) -> list[Cell]:
        return list(itertools.product(self.ranges, self.stops, self.targets))

    def index(self, cell: Cell) -> tuple[int, int, int]:
        r, s, t = cell
        return self.ranges.index(r), self.stops.index(s), self.targets.index(t)

    def cell_at(self, idx: tuple[int, int, int]) -> Cell:
        return self.ranges[idx[0]], self.stops[idx[1]], self.targets[idx[2]]


def grid_from_cfg(cfg) -> Grid:
    g = cfg.validation.grid
    return Grid(tuple(g.range_minutes), tuple(g.stop_range_fraction), tuple(g.target_r))


def primary_cell(cfg) -> Cell:
    return cfg.session.range_minutes, cfg.execution.stop_range_fraction, cfg.execution.target_r


# --------------------------------------------------------------------------------------
# Running the grid
# --------------------------------------------------------------------------------------
def load_dev_inputs(cfg, purpose: str):
    """Development-partition bars, daily table and tradable dates (guarded loaders: holdout stays locked)."""
    daily = load_daily(cfg, "dev", purpose=purpose)
    bars = load_bars_chosen(cfg, "dev", purpose=purpose)
    return bars, daily, daily.index[daily["tradable"]]


def run_grid(cfg, bars: pd.DataFrame, dates: pd.DatetimeIndex, grid: Grid, cost_scale: float | None = None) -> dict[Cell, pd.DataFrame]:
    """Trades for every grid cell. Signals depend only on the range length, so they are computed once per length."""
    out: dict[Cell, pd.DataFrame] = {}
    overrides = {} if cost_scale is None else {"cost_scale": cost_scale}
    for r in grid.ranges:
        orb = params_from_cfg(cfg, range_minutes=r)
        sig = generate_signals(bars, dates, orb, tz=cfg.session.timezone)
        for s, t in itertools.product(grid.stops, grid.targets):
            ex = exec_params_from_cfg(cfg, stop_fraction=s, target_r=t, **overrides)
            trades, _ = simulate(bars, sig, orb, ex, tz=cfg.session.timezone)
            out[(r, s, t)] = enrich(trades, cfg)
    return out


# --------------------------------------------------------------------------------------
# Pure selection logic
# --------------------------------------------------------------------------------------
def window_stats(trades_by_cell: dict[Cell, pd.DataFrame], grid: Grid, start, end) -> dict[str, np.ndarray]:
    """Per-cell mean R, number of trades, and standard error of mean R over trades dated within [start, end]."""
    shape = grid.shape
    mean, n, se = np.full(shape, np.nan), np.zeros(shape, dtype=int), np.full(shape, np.nan)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    for cell, t in trades_by_cell.items():
        r = t.loc[(t.index >= start) & (t.index <= end), "r_multiple"]
        i = grid.index(cell)
        n[i] = len(r)
        if len(r) > 1:
            mean[i], se[i] = r.mean(), r.std(ddof=1) / np.sqrt(len(r))
    return {"mean_r": mean, "n": n, "se": se}


def smooth(values: np.ndarray, radius: int = 1) -> np.ndarray:
    """Average each cell with every cell within ``radius`` grid steps in each dimension (a box). Cells outside the
    grid are simply absent (so edge cells average over fewer neighbours); NaNs are ignored."""
    out = np.full(values.shape, np.nan)
    for idx in np.ndindex(values.shape):
        sl = tuple(slice(max(i - radius, 0), i + radius + 1) for i in idx)
        block = values[sl]
        out[idx] = np.nan if np.isnan(block).all() else np.nanmean(block)
    return out


def select_cell(mean_r: np.ndarray, radius: int, min_score: float) -> tuple[tuple | None, np.ndarray]:
    """The pre-registered rule: highest smoothed score, but only if it exceeds ``min_score``; else None (sit out)."""
    sm = smooth(mean_r, radius)
    if np.isnan(sm).all():
        return None, sm
    best = np.unravel_index(np.nanargmax(sm), sm.shape)
    if not sm[best] > min_score:
        return None, sm
    return tuple(int(i) for i in best), sm


def walk_forward_plan(first_test: int, last_test: int, train_years: int, data_start) -> list[dict]:
    """Rolling windows: train = the ``train_years`` calendar years before the test year (clipped to the data start)."""
    plan = []
    for y in range(first_test, last_test + 1):
        train_start = max(pd.Timestamp(f"{y - train_years}-01-01"), pd.Timestamp(data_start))
        plan.append({"test_year": y, "train_start": train_start, "train_end": pd.Timestamp(f"{y - 1}-12-31"),
                     "test_start": pd.Timestamp(f"{y}-01-01"), "test_end": pd.Timestamp(f"{y}-12-31")})
    return plan


def walk_forward(trades_by_cell: dict[Cell, pd.DataFrame], grid: Grid, plan: list[dict], radius: int, min_score: float):
    """Select inside each training window only; return (per-fold table, stitched out-of-sample trades)."""
    rows, parts = [], []
    for f in plan:
        train = window_stats(trades_by_cell, grid, f["train_start"], f["train_end"])
        idx, sm = select_cell(train["mean_r"], radius, min_score)
        row = {"test_year": f["test_year"], "train": f"{f['train_start'].date()} to {f['train_end'].date()}", "cell": None if idx is None else grid.cell_at(idx),
               "train_smoothed_r": np.nan if idx is None else sm[idx], "train_raw_r": np.nan if idx is None else train["mean_r"][idx]}
        if idx is None:
            row.update(test_trades=0, test_r=np.nan)
        else:
            t = trades_by_cell[grid.cell_at(idx)]
            tt = t[(t.index >= f["test_start"]) & (t.index <= f["test_end"])].copy()
            tt["fold_year"], tt["cell"] = f["test_year"], [grid.cell_at(idx)] * len(tt)
            parts.append(tt)
            row.update(test_trades=len(tt), test_r=tt["r_multiple"].mean() if len(tt) else np.nan)
        rows.append(row)
    stitched = pd.concat(parts) if parts else pd.DataFrame()
    return pd.DataFrame(rows), stitched
