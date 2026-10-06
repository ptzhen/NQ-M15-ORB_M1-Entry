"""Stage 6 selection logic: pure-function tests (no market data) plus one integration check against Stage 5."""

import numpy as np
import pandas as pd
import pytest

from orb.analysis.backtest import run_backtest
from orb.analysis.sweep import (
    Grid, grid_from_cfg, primary_cell, run_grid, select_cell, smooth, walk_forward, walk_forward_plan, window_stats,
)
from orb.config import resolve


# ---------------------------------------------------------------- grid
def test_grid_is_the_preregistered_96_cells_and_contains_the_primary_spec(cfg):
    g = grid_from_cfg(cfg)
    assert g.shape == (6, 4, 4) and len(g.cells) == 96 == len(set(g.cells))
    assert primary_cell(cfg) == (15, 1.0, 1.0) and primary_cell(cfg) in g.cells
    assert all(g.cell_at(g.index(c)) == c for c in g.cells)
    assert None in g.targets                       # "no target" is one of the target options


# ---------------------------------------------------------------- smoothing
def test_smooth_averages_the_neighbourhood_including_edges():
    v = np.arange(27, dtype=float).reshape(3, 3, 3)
    s = smooth(v, 1)
    assert s[1, 1, 1] == v.mean() == 13.0                                   # centre sees every cell
    assert s[0, 0, 0] == v[0:2, 0:2, 0:2].mean()                            # corner sees only the 2x2x2 block that exists


def test_smooth_ignores_nan_and_keeps_all_nan_as_nan():
    v = np.full((3, 3, 3), np.nan)
    assert np.isnan(smooth(v, 1)).all()
    v[1, 1, 1] = 4.0
    s = smooth(v, 1)
    assert s[0, 0, 0] == 4.0 and s[2, 2, 2] == 4.0


# ---------------------------------------------------------------- the pre-registered rule
def test_rule_prefers_a_broad_plateau_over_an_isolated_spike():
    v = np.full((6, 4, 4), -0.05)
    v[0, 0, 0] = 0.50                                                       # lone spike in a corner
    v[2:5, 1:4, 1:4] = 0.10                                                 # plateau
    raw_best = np.unravel_index(np.nanargmax(v), v.shape)
    chosen, sm = select_cell(v, radius=1, min_score=0.0)
    assert raw_best == (0, 0, 0)                                            # a naive max would pick the spike ...
    assert chosen == (3, 2, 2)                                              # ... the rule picks the centre of the plateau
    assert sm[0, 0, 0] < sm[3, 2, 2]


def test_rule_sits_out_when_nothing_clears_the_bar():
    assert select_cell(np.full((6, 4, 4), -0.02), 1, 0.0)[0] is None
    v = np.zeros((6, 4, 4))                                                 # smoothed best == 0 is NOT above 0
    assert select_cell(v, 1, 0.0)[0] is None
    assert select_cell(np.full((6, 4, 4), np.nan), 1, 0.0)[0] is None


# ---------------------------------------------------------------- walk-forward structure
def test_walk_forward_plan_structure(cfg):
    plan = walk_forward_plan(2015, 2023, 5, "2010-06-01")
    assert [p["test_year"] for p in plan] == list(range(2015, 2024)) and len(plan) == 9
    assert plan[0]["train_start"] == pd.Timestamp("2010-06-01")             # clipped to the first data
    assert plan[1]["train_start"] == pd.Timestamp("2011-01-01")             # then exactly 5 calendar years
    for p in plan:
        assert p["train_end"] < p["test_start"]                             # training strictly precedes the test year
        assert p["test_start"].year == p["test_end"].year == p["test_year"]
    for a, b in zip(plan[:-1], plan[1:]):
        assert a["test_end"] < b["test_start"]                              # test windows do not overlap


def _synthetic_cells(train_r_good, test_r_good, seed=0):
    """Two cells on a (1,1,2) grid. Cell A looks great in 2010-2014 and the reverse in 2015; cell B the opposite."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2010-06-01", "2015-12-31")
    out = {}
    for cell, (tr, te) in {(15, 1.0, 1.0): (train_r_good, test_r_good), (15, 1.0, None): (-train_r_good, -test_r_good)}.items():
        mu = np.where(dates < pd.Timestamp("2015-01-01"), tr, te)
        out[cell] = pd.DataFrame({"r_multiple": mu + rng.normal(0, 0.01, len(dates))}, index=dates)
    return out, Grid((15,), (1.0,), (1.0, None))


def test_fold_selection_uses_only_the_training_window():
    cells, grid = _synthetic_cells(train_r_good=0.3, test_r_good=-0.4)
    plan = walk_forward_plan(2015, 2015, 5, "2010-06-01")
    folds, stitched = walk_forward(cells, grid, plan, radius=0, min_score=0.0)
    assert folds.loc[0, "cell"] == (15, 1.0, 1.0)                           # chosen because it won in training ...
    assert folds.loc[0, "test_r"] < -0.3                                    # ... even though it then loses in the test year
    assert (stitched["cell"] == (15, 1.0, 1.0)).all() and (stitched.index.year == 2015).all()


def test_rewriting_the_test_year_cannot_change_the_selection_but_rewriting_training_can():
    cells, grid = _synthetic_cells(0.3, -0.4)
    plan = walk_forward_plan(2015, 2015, 5, "2010-06-01")
    base, _ = walk_forward(cells, grid, plan, 0, 0.0)
    rewritten_test = {c: t.assign(r_multiple=np.where(t.index.year == 2015, -t["r_multiple"] * 10, t["r_multiple"])) for c, t in cells.items()}
    after_test, _ = walk_forward(rewritten_test, grid, plan, 0, 0.0)
    assert after_test.loc[0, "cell"] == base.loc[0, "cell"]                 # test year irrelevant to selection
    rewritten_train = {c: t.assign(r_multiple=np.where(t.index.year < 2015, -t["r_multiple"], t["r_multiple"])) for c, t in cells.items()}
    after_train, _ = walk_forward(rewritten_train, grid, plan, 0, 0.0)
    assert after_train.loc[0, "cell"] != base.loc[0, "cell"]                # training data DOES drive it (the check has teeth)


def test_fold_sits_out_when_the_training_window_has_no_edge():
    cells, grid = _synthetic_cells(0.3, 0.3)
    cells = {c: t.assign(r_multiple=-abs(t["r_multiple"])) for c, t in cells.items()}     # every cell loses in training
    folds, stitched = walk_forward(cells, grid, walk_forward_plan(2015, 2015, 5, "2010-06-01"), 0, 0.0)
    assert folds.loc[0, "cell"] is None and folds.loc[0, "test_trades"] == 0 and len(stitched) == 0


def test_window_stats_is_inclusive_and_counts_trades():
    cells, grid = _synthetic_cells(0.3, -0.4)
    w = window_stats(cells, grid, "2010-06-01", "2010-06-30")
    assert w["n"][0, 0, 0] == len(pd.bdate_range("2010-06-01", "2010-06-30"))
    assert w["mean_r"][0, 0, 0] == pytest.approx(0.3, abs=0.02)


# ---------------------------------------------------------------- integration with the real backtest
def test_grid_cells_reproduce_the_stage5_backtest(cfg):
    if not (resolve(cfg, cfg.paths.processed_dir) / "daily.parquet").exists():
        pytest.skip("run `python -m orb data` first")
    from orb.analysis.sweep import load_dev_inputs
    bars, daily, dates = load_dev_inputs(cfg, "unit test: grid vs backtest")
    grid = Grid((15, 30), (1.0, 0.5), (1.0, None))
    cells = run_grid(cfg, bars, dates, grid)
    assert max(t.index.max() for t in cells.values()) < pd.Timestamp(cfg.splits.holdout_start)
    ref = run_backtest(cfg, "dev", purpose="unit test: grid vs backtest").trades
    pd.testing.assert_series_equal(cells[(15, 1.0, 1.0)]["pnl_usd_nq"], ref["pnl_usd_nq"])
    ref30 = run_backtest(cfg, "dev", purpose="unit test: grid vs backtest", orb_overrides={"range_minutes": 30}, stop_fraction=0.5, target_r=None).trades
    pd.testing.assert_series_equal(cells[(30, 0.5, None)]["pnl_usd_nq"], ref30["pnl_usd_nq"])


# ---------------------------------------------------------------- the multiple-testing ledger
def test_trial_registry_counts_each_grid_cell_exactly_once(cfg):
    import json
    path = resolve(cfg, cfg.paths.trials_log)
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    keys = [r["key"] for r in recs]
    assert len(keys) == len(set(keys))                                          # no duplicates anywhere
    grid_recs = [r for r in recs if r["stage"] in ("stage4", "stage6")]
    assert len(grid_recs) == 96                                                 # primary counted once + 95 grid cells
    combos = {(r["params"]["range_minutes"], r["params"]["stop_range_fraction"], r["params"]["target_r"]) for r in grid_recs}
    assert combos == set(grid_from_cfg(cfg).cells)                              # exactly the pre-registered grid
    assert {r["name"] for r in recs if r["stage"] == "stage7"} <= {"hypothesis_vol_high_tercile", "hypothesis_breakout_direction"}
