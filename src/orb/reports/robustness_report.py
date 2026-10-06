"""Stage 7 report: how much of the primary result could be noise, and the two pre-registered hypotheses.

Executes docs/DECISIONS.md D6 without deviation, on DEVELOPMENT data only. Verdicts use the thresholds fixed in D6.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from orb.analysis.metrics import drawdown_stats
from orb.analysis.robustness import (
    assign_regime, bootstrap_stat, daily_returns, deflated_sharpe, direction_permutation_test, freeze_terciles, mean_rows,
    percentile_ci, permuted_max_drawdowns, prior_volatility, profit_factor_rows, reality_check, sharpe, sharpe_rows, sortino,
    sortino_rows, stationary_bootstrap_indices, win_rate_rows,
)
from orb.analysis.sweep import Grid, grid_from_cfg, load_dev_inputs, primary_cell, run_grid
from orb.analysis.trials import count_trials, record_trial
from orb.config import resolve
from orb.reports.data_quality import BLUE, INK, INK2, MUTED_BAR, ORANGE, SURFACE, _style, md_table
from orb.reports.validation_report import fmt_cell
from orb.signals.orb import generate_signals, params_from_cfg

THRESHOLDS_FILE = "logs/frozen_vol_thresholds.json"


def slice_bootstrap(r: np.ndarray, labels: np.ndarray, cats: list, resamples: int, mean_block: float, rng: np.random.Generator, chunk: int = 2000) -> dict:
    """Block-bootstrap distribution of mean(r) within each category (the same resamples for every category, so differences are paired)."""
    codes = pd.Categorical(labels, categories=cats).codes
    out = {c: [] for c in cats}
    for lo in range(0, resamples, chunk):
        k = min(chunk, resamples - lo)
        idx = stationary_bootstrap_indices(len(r), k, mean_block, rng)
        rr, ll = r[idx], codes[idx]
        for ci, c in enumerate(cats):
            m = ll == ci
            cnt = m.sum(axis=1)
            out[c].append(np.where(cnt > 0, (rr * m).sum(axis=1) / np.maximum(cnt, 1), np.nan))
    return {c: np.concatenate(v) for c, v in out.items()}


def _ci(dist: np.ndarray) -> tuple[float, float]:
    d = dist[~np.isnan(dist)]
    return percentile_ci(d) if len(d) else (np.nan, np.nan)


def build_robustness_report(cfg) -> str:
    out = resolve(cfg, cfg.paths.output_dir) / "robustness"
    out.mkdir(parents=True, exist_ok=True)
    _style()
    rb = cfg.robustness
    B, L = rb.bootstrap.resamples, rb.bootstrap.mean_block_days
    rng = np.random.default_rng(cfg.seed)
    grid, prim = grid_from_cfg(cfg), primary_cell(cfg)
    bars, daily, dates = load_dev_inputs(cfg, "stage 7 robustness (development data, pre-registered protocol D6)")
    d0, d1 = dates.min(), dates.max()
    pv = cfg.data.point_value_usd.NQ

    net = run_grid(cfg, bars, dates, grid)
    tp = net[prim]                                                    # primary-spec trade log, 1x costs
    r, pnl = tp["r_multiple"].to_numpy(), tp["pnl_usd_nq"].to_numpy()
    n_tr = len(tp)
    gross_primary = run_grid(cfg, bars, dates, Grid((prim[0],), (prim[1],), (prim[2],)), cost_scale=0.0)[prim]["r_multiple"].to_numpy()

    # ---------------------------------------------------------------- 1. bootstrap
    dr = daily_returns(tp, dates, pv).to_numpy()                      # daily net return on notional, zeros on no-trade days
    boot = {
        "expectancy (R)": (bootstrap_stat(r, mean_rows, B, L, rng), r.mean()),
        "win rate": (bootstrap_stat(r, win_rate_rows, B, L, rng), (r > 0).mean()),
        "profit factor": (bootstrap_stat(pnl, profit_factor_rows, B, L, rng), pnl[pnl > 0].sum() / -pnl[pnl < 0].sum()),
        "Sharpe (annualised, daily)": (bootstrap_stat(dr, sharpe_rows, B, L, rng), sharpe(dr)),
        "Sortino (annualised, daily)": (bootstrap_stat(dr, sortino_rows, B, L, rng), sortino(dr)),
    }
    iid_exp = bootstrap_stat(r, mean_rows, B, 1, rng)
    gross_exp = bootstrap_stat(gross_primary, mean_rows, B, L, rng)
    exp_dist = boot["expectancy (R)"][0]
    p_pos = float((exp_dist > 0).mean())
    rows = []
    for k, (dist, point) in boot.items():
        lo, hi = percentile_ci(dist)
        rows.append((k, f"{point:.3f}" if k != "win rate" else f"{point:.1%}", f"{lo:.3f} to {hi:.3f}" if k != "win rate" else f"{lo:.1%} to {hi:.1%}"))
    lo, hi = percentile_ci(iid_exp)
    rows.append(("expectancy (R), plain i.i.d. bootstrap (for comparison)", f"{r.mean():.3f}", f"{lo:.3f} to {hi:.3f}"))
    lo, hi = percentile_ci(gross_exp)
    rows.append(("expectancy (R), frictionless (0x costs)", f"{gross_primary.mean():.3f}", f"{lo:.3f} to {hi:.3f}"))
    boot_tbl = pd.DataFrame(rows, columns=["Primary spec, 1x costs", "point estimate", f"95% interval (block bootstrap, mean block {L} days)"])

    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    bins = np.linspace(min(exp_dist.min(), iid_exp.min()), max(exp_dist.max(), iid_exp.max()), 70)
    ax.hist(exp_dist, bins=bins, color=BLUE, alpha=0.9, edgecolor=SURFACE, linewidth=0.6, label=f"Block bootstrap (mean block {L} days)")
    ax.hist(iid_exp, bins=bins, color=MUTED_BAR, alpha=0.7, edgecolor=SURFACE, linewidth=0.6, label="Plain i.i.d. bootstrap")
    ax.axvline(0, color=ORANGE, lw=2)
    ax.text(0, ax.get_ylim()[1] * 0.95, "  zero", color=ORANGE, va="top", fontsize=9)
    ax.set_xlabel("Resampled expectancy (R per trade)"); ax.set_ylabel("Resamples")
    ax.set_title("How uncertain is the primary spec's expectancy?")
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout(); fig.savefig(out / "fig14_bootstrap_expectancy.png", dpi=140); plt.close(fig)

    # ---------------------------------------------------------------- 2. trade-order Monte Carlo
    obs_dd = drawdown_stats(pd.Series(pnl, index=tp.index))["max_drawdown"]
    perm_dd = permuted_max_drawdowns(pnl, rb.trade_order_permutations, rng)
    dd_pct = float((perm_dd <= obs_dd).mean())
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    ax.hist(perm_dd, bins=60, color=BLUE, edgecolor=SURFACE, linewidth=0.5)
    ax.axvline(obs_dd, color=ORANGE, lw=2)
    ax.text(obs_dd, ax.get_ylim()[1] * 0.92, "  realised order", color=ORANGE, fontsize=9, va="top")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_xlabel("Maximum drawdown, 1 NQ"); ax.set_ylabel("Random re-orderings of the same trades")
    ax.set_title("Is the realised drawdown just bad luck in trade order?")
    fig.tight_layout(); fig.savefig(out / "fig15_drawdown_monte_carlo.png", dpi=140); plt.close(fig)

    # ---------------------------------------------------------------- 3. volatility regime (D3), thresholds frozen
    pv_all = prior_volatility(bars, rb.volatility.lookback_days)
    vol_on_trades = pv_all.reindex(tp.index)
    thr = freeze_terciles(vol_on_trades, rb.volatility.quantiles)
    (resolve(cfg, THRESHOLDS_FILE)).parent.mkdir(parents=True, exist_ok=True)
    resolve(cfg, THRESHOLDS_FILE).write_text(json.dumps({
        "measure": f"mean (RTH high - low)/close over the {rb.volatility.lookback_days} trading days before the trade date",
        "low_mid_threshold": thr[0], "mid_high_threshold": thr[1],
        "computed_on": f"development trade days {tp.index.min().date()} to {tp.index.max().date()} (n={int(vol_on_trades.notna().sum())})",
        "frozen_by": "Stage 7 / D6; reuse these exact numbers on any later data"}, indent=2), encoding="utf-8")
    regime = assign_regime(vol_on_trades, thr)
    tp = tp.assign(vol_regime=regime.to_numpy())

    # ---------------------------------------------------------------- 4. slices with block-bootstrap intervals
    slices = {
        "Year": (tp.index.year.astype(str).to_numpy(), [str(y) for y in sorted(set(tp.index.year))]),
        "Side": (tp["side"].to_numpy(), ["long", "short"]),
        "Weekday": (tp["weekday"].astype(str).to_numpy(), ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
        "Prior volatility": (tp["vol_regime"].fillna("n/a").to_numpy(), ["low", "mid", "high"]),
    }
    forest, vol_dists = [("All trades", "Overall", r.mean(), *_ci(exp_dist), n_tr)], None
    for gname, (labels, cats) in slices.items():
        d = slice_bootstrap(r, labels, cats, B, L, rng)
        if gname == "Prior volatility":
            vol_dists = d
        for c in cats:
            m = labels == c
            lo, hi = _ci(d[c])
            forest.append((gname, c, r[m].mean() if m.any() else np.nan, lo, hi, int(m.sum())))
    forest_df = pd.DataFrame(forest, columns=["group", "slice", "mean_r", "lo", "hi", "n"])

    fig, ax = plt.subplots(figsize=(8.5, 9.0))
    ypos = np.arange(len(forest_df))[::-1]
    ax.hlines(ypos, forest_df["lo"], forest_df["hi"], color=INK2, lw=1.6)
    ax.scatter(forest_df["mean_r"], ypos, color=BLUE, s=34, zorder=3, edgecolor=SURFACE, linewidth=1.2)
    ax.axvline(0, color=ORANGE, lw=1.6)
    ax.set_yticks(ypos, [f"{g}: {s}  (n={n:,})" if g != "All trades" else f"All trades  (n={n:,})" for g, s, n in zip(forest_df["group"], forest_df["slice"], forest_df["n"])], fontsize=8)
    ax.set_xlabel("Expectancy (R per trade), 95% block-bootstrap interval")
    ax.set_title("Primary spec by slice: how much is distinguishable from zero?")
    ax.grid(axis="y", visible=False)
    fig.tight_layout(); fig.savefig(out / "fig16_forest_slices.png", dpi=140); plt.close(fig)

    def _fmt(sub):
        d = sub.copy()
        d["trades"] = d["n"].map("{:,}".format)
        d["expectancy (R)"] = d["mean_r"].map("{:+.3f}".format)
        d["95% block interval (R)"] = [f"{a:+.3f} to {b:+.3f}" for a, b in zip(d["lo"], d["hi"])]
        d["includes 0?"] = np.where((d["lo"] <= 0) & (d["hi"] >= 0), "yes", "NO")
        return d[["slice", "trades", "expectancy (R)", "95% block interval (R)", "includes 0?"]]

    n_year_sig = int(((forest_df[forest_df.group == "Year"]["lo"] > 0) | (forest_df[forest_df.group == "Year"]["hi"] < 0)).sum())

    # ---- D3: volatility hypothesis
    hi_lo, hi_hi = _ci(vol_dists["high"])
    diff = vol_dists["high"] - vol_dists["low"]
    d_lo, d_hi = _ci(diff)
    h_vol_supported = hi_lo > 0
    vol_tbl = _fmt(forest_df[forest_df.group == "Prior volatility"])
    record_trial(cfg, "stage7", "hypothesis_vol_high_tercile", {"measure": "20d mean RTH range/close, prior days", "terciles": "frozen on dev", "spec": "primary", "test": "high tercile expectancy > 0"},
                 {"high_expectancy_r": float(r[tp["vol_regime"].to_numpy() == "high"].mean())})

    # ---------------------------------------------------------------- 5. is the breakout direction informative? (D5)
    orb = params_from_cfg(cfg)
    sig = generate_signals(bars, dates, orb, tz=cfg.session.timezone)
    sg = sig[sig["direction"] != 0]
    from orb.signals.orb import day_matrices
    mats = day_matrices(bars, pd.DatetimeIndex(sg.index), orb)
    rows_ix = np.arange(len(sg))
    entry_open = sg["entry_open"].to_numpy()
    flat_open = mats["open"][rows_ix, orb.n_bars - 1]
    move_bps = (flat_open - entry_open) / entry_open * 1e4
    dirn = sg["direction"].to_numpy()
    dtest = direction_permutation_test(move_bps, dirn, rb.direction_permutations, rng)
    h_dir_supported = dtest["p_value"] < 0.05
    record_trial(cfg, "stage7", "hypothesis_breakout_direction", {"statistic": "mean direction x hold-to-close bps", "spec": "primary signals", "test": "direction-label permutation, one-sided"},
                 {"observed_bps": dtest["observed_mean_bps"], "p": dtest["p_value"]})
    dir_tbl = pd.DataFrame(
        [("Mean hold-to-close return in the signal direction (gross, bps of price)", f"{dtest['observed_mean_bps']:+.2f}"),
         ("Same, if direction labels were random (mean +/- sd of 20,000 shuffles)", f"{dtest['null_mean_bps']:+.2f} +/- {dtest['null_sd_bps']:.2f}"),
         ("One-sided permutation p-value", f"{dtest['p_value']:.3f}"),
         ("Mean over long signals only / short signals only (bps, in trade direction)", f"{dtest['long_mean_bps']:+.2f} / {dtest['short_mean_bps']:+.2f}"),
         ("Always-long benchmark, same entry times (bps)", f"{dtest['always_long_mean_bps']:+.2f}")], columns=["Hold-to-close test, primary signals", "value"])

    # the gross-positive cluster from Stage 6, descriptively
    from orb.analysis.sweep import window_stats
    cluster_cell = grid.cell_at(np.unravel_index(np.nanargmax(window_stats(net, grid, d0, d1)["mean_r"]), grid.shape))
    ct = net[cluster_cell]
    ccl = []
    for side in ("long", "short"):
        s_ = ct[ct["side"] == side]
        ccl.append((side, f"{len(s_):,}", f"{s_['r_multiple'].mean():+.3f}", f"{s_['net_points_nq'].mean():+.2f}", f"{s_['pnl_usd_nq'].sum():+,.0f}"))
    ccl.append(("all", f"{len(ct):,}", f"{ct['r_multiple'].mean():+.3f}", f"{ct['net_points_nq'].mean():+.2f}", f"{ct['pnl_usd_nq'].sum():+,.0f}"))
    cluster_tbl = pd.DataFrame(ccl, columns=[f"Best net cell: {fmt_cell(cluster_cell)}", "trades", "expectancy (R)", "avg net points", "$ (1 NQ)"])

    # ---------------------------------------------------------------- 6. multiple testing on the 96 cells
    cell_list = grid.cells
    mat = np.column_stack([daily_returns(net[c], dates, pv).to_numpy() for c in cell_list])
    sr_daily = mat.mean(axis=0) / mat.std(axis=0, ddof=1)
    v_sr = float(np.var(sr_daily, ddof=1))
    n_trials = count_trials(cfg)
    best_k = int(np.argmax(sr_daily))
    prim_k = cell_list.index(prim)
    dsr_best, dsr_prim = deflated_sharpe(mat[:, best_k], n_trials, v_sr), deflated_sharpe(mat[:, prim_k], n_trials, v_sr)
    rc = reality_check(mat, rb.reality_check.resamples, rb.reality_check.mean_block_days, rng)
    best_distinguishable = dsr_best["dsr"] >= 0.95 and rc["p_value"] < 0.05
    prim_distinguishable = dsr_prim["dsr"] >= 0.95 and rc["p_value"] < 0.05
    mt_tbl = pd.DataFrame(
        {
            f"Best-Sharpe cell": [fmt_cell(cell_list[best_k]), f"{dsr_best['sharpe_annual']:+.2f}", f"{dsr_best['benchmark_annual']:+.2f}", f"{dsr_best['psr_vs_zero']:.3f}", f"{dsr_best['dsr']:.3f}", "yes" if best_distinguishable else "NO"],
            "Primary spec": [fmt_cell(prim), f"{dsr_prim['sharpe_annual']:+.2f}", f"{dsr_prim['benchmark_annual']:+.2f}", f"{dsr_prim['psr_vs_zero']:.3f}", f"{dsr_prim['dsr']:.3f}", "yes" if prim_distinguishable else "NO"],
        },
        index=["cell", "annualised Sharpe (net, 1x)", "Sharpe expected from the best of N skill-less trials", "probability true Sharpe > 0 (no deflation)", "DEFLATED Sharpe ratio (need >= 0.95)", "distinguishable from noise (DSR >= 0.95 AND RC p < 0.05)"],
    )
    mt_tbl.index.name = f"N = {n_trials} trials"

    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    ax.hist(rc["null_maxima"], bins=60, color=MUTED_BAR, edgecolor=SURFACE, linewidth=0.5)
    ax.axvline(rc["observed"], color=ORANGE, lw=2)
    ax.text(rc["observed"], ax.get_ylim()[1] * 0.92, "  best of 96, observed", color=ORANGE, fontsize=9, va="top")
    ax.set_xlabel("Maximum over 96 cells of sqrt(T) x mean daily return"); ax.set_ylabel("Resamples under 'no edge anywhere'")
    ax.set_title(f"White's Reality Check: p = {rc['p_value']:.3f}")
    fig.tight_layout(); fig.savefig(out / "fig17_reality_check.png", dpi=140); plt.close(fig)

    pd.DataFrame({"cell": [fmt_cell(c) for c in cell_list], "sharpe_annual": sr_daily * np.sqrt(252)}).to_csv(out / "cell_sharpes.csv", index=False, float_format="%.4f")

    # ---------------------------------------------------------------- report
    exp_lo, exp_hi = percentile_ci(exp_dist)
    width_ratio = (exp_hi - exp_lo) / (percentile_ci(iid_exp)[1] - percentile_ci(iid_exp)[0])
    n_slices_flagged = int((~((forest_df['lo'] <= 0) & (forest_df['hi'] >= 0)))[forest_df['group'] != 'All trades'].sum())
    n_slices = int((forest_df['group'] != 'All trades').sum())
    n_flag_neg = int((forest_df['hi'] < 0)[forest_df['group'] != 'All trades'].sum())
    md = f"""# Robustness report (Stage 7)

Development data only ({d0.date()} to {d1.date()}); the 2024+ holdout was **not** loaded. This executes, without deviation, the protocol committed before any result existed
(`docs/DECISIONS.md`, D6, commit `7ad355d`). Primary specification, 1x costs, unless stated. Trials in the registry: **{n_trials}** (96 grid cells + the 2 hypotheses of this stage).
Resamples: {B:,} stationary-bootstrap draws, mean block {L} trading days, seed {cfg.seed}.

## 1. Bootstrap: how uncertain is the primary result?

{md_table(boot_tbl, index=False)}

* The 95% interval for expectancy is **{exp_lo:+.3f} to {exp_hi:+.3f} R**; in {p_pos:.1%} of resamples the expectancy was above zero.
  {"The interval excludes zero on the negative side: the primary spec lost money after costs more consistently than luck would explain." if exp_hi < 0 else ("The interval includes zero: the data cannot distinguish the primary spec from a break-even strategy." if exp_lo <= 0 <= exp_hi else "The interval is entirely above zero.")}
* The block bootstrap interval is **{width_ratio:.2f}x** as wide as the plain i.i.d. one. {"Results cluster in time, so treating trades as independent would understate the uncertainty." if width_ratio > 1.1 else "So trade-by-trade results show little serial dependence at this horizon: the i.i.d. interval was not misleadingly narrow here. (Slower, regime-scale effects are examined by year and by volatility below.)"}
* Frictionless, the expectancy is {gross_primary.mean():+.3f} R (interval {percentile_ci(gross_exp)[0]:+.3f} to {percentile_ci(gross_exp)[1]:+.3f}).

![bootstrap](fig14_bootstrap_expectancy.png)

## 2. Monte Carlo of trade order

Realised maximum drawdown: **${obs_dd:,.0f}** (1 NQ). Across {rb.trade_order_permutations:,} random re-orderings of the same {n_tr:,} trades the maximum drawdown had median **${np.median(perm_dd):,.0f}**
and 5th-95th percentile ${np.quantile(perm_dd, 0.05):,.0f} to ${np.quantile(perm_dd, 0.95):,.0f}. The realised drawdown is at the **{dd_pct:.0%}** percentile.
{"It is far worse than a random ordering would produce, meaning losses are **clustered in time** (regime dependence), not spread evenly." if dd_pct > 0.95 else ("It sits inside the range of random orderings, so the drawdown itself is not evidence of time clustering." if dd_pct > 0.05 else "It is better than almost every random ordering (gains clustered early, losses spread).")}
(Because total P&L is negative, every ordering ends at the same deficit; the shuffle shows how much worse or better the path could have been.)

![drawdown](fig15_drawdown_monte_carlo.png)

## 3. Slices with honest intervals

{n_year_sig} of {len(forest_df[forest_df.group == 'Year'])} individual years have a block-bootstrap interval that excludes zero (about {0.05 * len(forest_df[forest_df.group == 'Year']):.1f} would be expected by chance alone if every year had a true expectancy equal to the overall figure of 0 R).

![forest](fig16_forest_slices.png)

{md_table(_fmt(forest_df[forest_df.group == 'Year']), index=False)}

{md_table(_fmt(forest_df[forest_df.group == 'Side']), index=False)}

{md_table(_fmt(forest_df[forest_df.group == 'Weekday']), index=False)}

Across all {n_slices} slices in this section (years, side, weekday, volatility), {n_slices_flagged} have an interval excluding zero, {n_flag_neg} of them on the negative side. Because the overall expectancy is itself negative, negative slices are expected and are not independent findings. Each slice is a chance for a false positive; nothing here is used to select anything.

## 4. Hypothesis D3: does it work better when prior volatility is high?

Measure (fixed in D6): {rb.volatility.lookback_days}-day mean of (RTH high - low)/close **before** the trade date. Thresholds frozen from development trade days and saved to `{THRESHOLDS_FILE}`:
low/mid cut **{thr[0] * 100:.3f}%**, mid/high cut **{thr[1] * 100:.3f}%** of price.

{md_table(vol_tbl, index=False)}

* High minus low terciles: **{np.nanmean(diff):+.3f} R**, 95% block interval **{d_lo:+.3f} to {d_hi:+.3f}**.
* Verdict under the D6 rule (high-tercile lower bound above zero): **{"SUPPORTED on development data" if h_vol_supported else "NOT supported on development data"}** (lower bound {hi_lo:+.3f} R).
  {"It earns a holdout test, nothing more." if h_vol_supported else "It will still be run once on the holdout, as declared in D6, so the answer there is on record either way."}

## 5. Hypothesis D5: is the breakout *direction* informative, or is it market drift?

From each primary-spec entry to the {cfg.session.flat_time} open, with no stop, no target and no costs:

{md_table(dir_tbl, index=False)}

* Verdict under the D6 rule (p < 0.05): **{"SUPPORTED on development data: direction carries information beyond drift" if h_dir_supported else "NOT supported on development data: the breakout direction does not predict the rest of the day better than shuffled direction labels"}**.
  Shuffled labels share the same drift, so a good result cannot be produced by the market simply rising.

For the best net cell of the grid (the "no target + tight stop" cluster flagged in Stage 6), descriptively, by side:

{md_table(cluster_tbl, index=False)}

## 6. Multiple testing (96 cells, 1x costs, daily returns on notional)

{md_table(mt_tbl)}

* **White's Reality Check** (best of 96 against "no cell has positive expected return"): observed statistic {rc['observed']:.4f}, **p = {rc['p_value']:.3f}**.
* Variance of Sharpe across cells (daily units) used for the deflation: {v_sr:.2e}.
* Under the D6 thresholds the best cell is **{"distinguishable" if best_distinguishable else "NOT distinguishable"}** from noise, and the primary spec is **{"distinguishable" if prim_distinguishable else "NOT distinguishable"}**.

![reality check](fig17_reality_check.png)

## 7. What this does and does not show

* All of this is development data and one history. It can say whether the *observed* results are compatible with no edge; it cannot say what the future holds.
* The deflation counts {n_trials} trials. The true number of ideas considered is at least that.
* Costs are assumptions (1 tick, $2/side). Frictionless and 2x results are in Stage 4 and Stage 6.
* Hypotheses D3 and D5 were formed after seeing development results; their holdout tests (Stage 8) are the real evidence.
"""
    path = out / "robustness_report.md"
    path.write_text(md, encoding="utf-8")
    return str(path)
