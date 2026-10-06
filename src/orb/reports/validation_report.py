"""Stage 6 report: parameter sensitivity, in/out-of-sample split and walk-forward.

Executes the protocol pre-registered in docs/DECISIONS.md (D4) without deviation, on DEVELOPMENT data only.
Every grid cell is recorded as a trial. The promotion verdict is computed mechanically from the D4 criteria.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle
from scipy import stats

from orb.analysis.metrics import summarize
from orb.analysis.sweep import (
    Cell, Grid, grid_from_cfg, load_dev_inputs, primary_cell, run_grid, select_cell, walk_forward, walk_forward_plan, window_stats,
)
from orb.analysis.trials import count_trials, record_trial
from orb.config import resolve
from orb.execution.simulate import exec_params_from_cfg, simulate
from orb.reports.data_quality import BLUE, INK, INK2, ORANGE, SURFACE, _style, md_table
from orb.signals.orb import generate_signals, params_from_cfg

CMAP = LinearSegmentedColormap.from_list("div", [ORANGE, "#f1f0ec", BLUE])   # two hues, neutral midpoint at 0


def fmt_cell(c: Cell | None) -> str:
    if c is None:
        return "sit out"
    r, s, t = c
    return f"{r} min, stop {s:g}, " + ("no target" if t is None else f"{t:g}R target")


def _mean_se(r: pd.Series) -> tuple[float, float]:
    return (float(r.mean()), float(r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 1 else (np.nan, np.nan)


def _heat(values: np.ndarray, grid: Grid, title: str, path, primary: Cell, selected: Cell | None, vmax: float) -> None:
    fig, axes = plt.subplots(1, len(grid.targets), figsize=(13, 3.9), sharey=True)
    im = None
    for k, t in enumerate(grid.targets):
        ax = axes[k]
        m = values[:, :, k].T                                            # rows = stop fraction, cols = range length
        im = ax.imshow(m, cmap=CMAP, vmin=-vmax, vmax=vmax, aspect="auto", origin="lower")
        for (si, ri), v in np.ndenumerate(m):
            if np.isfinite(v):
                ax.text(ri, si, f"{v:+.2f}", ha="center", va="center", fontsize=8, color="white" if abs(v) > 0.65 * vmax else INK)
        for cell, lw, mark in ((primary, 2.5, None), (selected, 0, "*")):
            if cell is not None and cell[2] == t:
                ri, si = grid.ranges.index(cell[0]), grid.stops.index(cell[1])
                if mark:
                    ax.text(ri + 0.33, si + 0.3, mark, ha="center", va="center", fontsize=14, color=INK)
                else:
                    ax.add_patch(Rectangle((ri - 0.5, si - 0.5), 1, 1, fill=False, ec=INK, lw=lw))
        ax.set_xticks(range(len(grid.ranges)), [str(r) for r in grid.ranges], fontsize=8)
        ax.set_yticks(range(len(grid.stops)), [f"{s:g}" for s in grid.stops], fontsize=8)
        ax.set_title("no target" if t is None else f"{t:g}R target", fontsize=10)
        ax.set_xlabel("Range length (min)", fontsize=8)
        ax.grid(False)
    axes[0].set_ylabel("Stop (fraction of range)", fontsize=8)
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold", fontsize=11)
    cb = fig.colorbar(im, ax=axes, shrink=0.8, pad=0.01)
    cb.set_label("Expectancy (R per trade)", fontsize=8)
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def build_validation_report(cfg) -> str:
    out = resolve(cfg, cfg.paths.output_dir) / "validation"
    out.mkdir(parents=True, exist_ok=True)
    _style()
    v = cfg.validation
    grid, prim = grid_from_cfg(cfg), primary_cell(cfg)
    radius, min_score = v.selection.smoothing_radius, v.selection.min_smoothed_expectancy_r
    bars, daily, dates = load_dev_inputs(cfg, "stage 6 validation (development data, pre-registered protocol D4)")
    d0, d1 = dates.min(), dates.max()

    net = run_grid(cfg, bars, dates, grid)                      # 1x costs
    gross = run_grid(cfg, bars, dates, grid, cost_scale=0.0)    # frictionless, for diagnosis only

    # ---- trials: every cell, once ----
    primary_params = {"range_minutes": prim[0], "stop_range_fraction": prim[1], "target_r": prim[2],
                      "target_fill": cfg.execution.target_fill, "ambiguity": cfg.execution.ambiguity, "last_entry_bar": cfg.signal.last_entry_bar}
    for cell, t in net.items():
        params = dict(primary_params, range_minutes=cell[0], stop_range_fraction=cell[1], target_r=cell[2])
        stage, name = ("stage4", "primary_spec") if cell == prim else ("stage6", "grid_cell")
        record_trial(cfg, stage, name, params, {"dev_expectancy_r_1x": float(t["r_multiple"].mean()), "dev_trades": len(t)})

    # ---- full development period ----
    full = window_stats(net, grid, d0, d1)
    full_g = window_stats(gross, grid, d0, d1)
    mean_net = full["mean_r"]
    pos_net, pos_gross = float((mean_net > 0).mean()), float((full_g["mean_r"] > 0).mean())
    t_stat = mean_net / full["se"]
    best_raw = grid.cell_at(np.unravel_index(np.nanargmax(mean_net), mean_net.shape))
    rng = np.random.default_rng(cfg.seed)
    null_max = float(np.mean(rng.standard_normal((20000, len(grid.cells))).max(axis=1)))
    sel_idx, sm_full = select_cell(mean_net, radius, min_score)
    sel_full = None if sel_idx is None else grid.cell_at(sel_idx)

    drag = full_g["mean_r"] - mean_net                           # R given up to costs, per cell
    drag_by_range = pd.Series({f"{r} min": float(np.nanmean(drag[i])) for i, r in enumerate(grid.ranges)})
    drag_by_stop = pd.Series({f"stop {s:g}": float(np.nanmean(drag[:, j])) for j, s in enumerate(grid.stops)})
    drag_tbl = pd.DataFrame({"avg R lost to costs": pd.concat([drag_by_range, drag_by_stop]).map("{:.3f}".format)})
    drag_tbl.index.name = "grid slice (averaged over the other parameters)"
    vmax = float(np.ceil(np.nanmax(np.abs(np.r_[mean_net.ravel(), full_g["mean_r"].ravel()])) * 20) / 20)
    _heat(mean_net, grid, "Net expectancy by parameter cell, development 2010-2023, 1x costs (box = primary spec, star = rule-selected)", out / "fig10_heatmap_net.png", prim, sel_full, vmax)
    _heat(full_g["mean_r"], grid, "Frictionless (0x costs) expectancy: is there anything to lose to costs?", out / "fig11_heatmap_gross.png", prim, None, vmax)

    cells_tbl = pd.DataFrame(
        [{"cell": fmt_cell(c), "trades": int(full["n"][grid.index(c)]), "expectancy (R)": mean_net[grid.index(c)], "naive t": t_stat[grid.index(c)]} for c in grid.cells]
    ).sort_values("expectancy (R)", ascending=False)

    def _fmt(df):
        d = df.copy()
        d["expectancy (R)"] = d["expectancy (R)"].map("{:+.3f}".format)
        d["naive t"] = d["naive t"].map("{:+.2f}".format)
        d["trades"] = d["trades"].map("{:,}".format)
        return d

    top, bottom = _fmt(cells_tbl.head(5)), _fmt(cells_tbl.tail(5))

    # ---- in-sample / out-of-sample ----
    isw, oosw = (v.is_oos.is_start, v.is_oos.is_end), (v.is_oos.oos_start, v.is_oos.oos_end)
    is_s, oos_s = window_stats(net, grid, *isw), window_stats(net, grid, *oosw)
    is_idx, is_sm = select_cell(is_s["mean_r"], radius, min_score)
    is_cell = None if is_idx is None else grid.cell_at(is_idx)
    ok = np.isfinite(is_s["mean_r"]) & np.isfinite(oos_s["mean_r"])
    rho, rho_p = stats.spearmanr(is_s["mean_r"][ok], oos_s["mean_r"][ok])
    is_grid_mean, oos_grid_mean_all = float(np.nanmean(is_s["mean_r"])), float(np.nanmean(oos_s["mean_r"]))
    is_pos, oos_pos = float((is_s["mean_r"] > 0).mean()), float((oos_s["mean_r"] > 0).mean())
    top_is = np.argsort(-np.where(ok, is_s["mean_r"], -np.inf).ravel())[:10]
    top10_oos = float(np.nanmean(oos_s["mean_r"].ravel()[top_is]))
    grid_oos_mean = float(np.nanmean(oos_s["mean_r"]))
    pi = grid.index(prim)
    rows = [("Primary spec", fmt_cell(prim), is_s["mean_r"][pi], oos_s["mean_r"][pi], int(oos_s["n"][pi]))]
    oos_sel = np.nan
    if is_cell is not None:
        si = grid.index(is_cell)
        oos_sel = oos_s["mean_r"][si]
        rows.append(("Rule-selected on IS", fmt_cell(is_cell), is_s["mean_r"][si], oos_s["mean_r"][si], int(oos_s["n"][si])))
    best_is = grid.cell_at(np.unravel_index(np.nanargmax(is_s["mean_r"]), is_s["mean_r"].shape))
    bi = grid.index(best_is)
    rows.append(("Best raw cell on IS (illustration only)", fmt_cell(best_is), is_s["mean_r"][bi], oos_s["mean_r"][bi], int(oos_s["n"][bi])))
    isoos_tbl = pd.DataFrame(rows, columns=["", "cell", "IS expectancy (R)", "OOS expectancy (R)", "OOS trades"])
    for c in ("IS expectancy (R)", "OOS expectancy (R)"):
        isoos_tbl[c] = isoos_tbl[c].map("{:+.3f}".format)

    fig, ax = plt.subplots(figsize=(6.4, 5.0))
    ax.scatter(is_s["mean_r"][ok], oos_s["mean_r"][ok], s=34, color=BLUE, edgecolor=SURFACE, linewidth=1.2, zorder=3, label="One of 96 cells")
    ax.scatter([is_s["mean_r"][pi]], [oos_s["mean_r"][pi]], s=90, color=ORANGE, edgecolor=SURFACE, linewidth=1.5, zorder=4, label="Primary spec")
    ax.axhline(0, color=INK2, lw=0.8); ax.axvline(0, color=INK2, lw=0.8)
    ax.set_xlabel("In-sample expectancy, 2010-2018 (R)"); ax.set_ylabel("Out-of-sample expectancy, 2019-2023 (R)")
    ax.set_title(f"In-sample vs out-of-sample, 96 cells (rank corr. {rho:+.2f})", fontsize=11)
    ax.legend(frameon=False, loc="best")
    fig.tight_layout(); fig.savefig(out / "fig12_is_vs_oos.png", dpi=140); plt.close(fig)

    # ---- walk-forward ----
    plan = walk_forward_plan(v.walk_forward.first_test_year, v.walk_forward.last_test_year, v.walk_forward.train_years, d0)
    folds, wf = walk_forward(net, grid, plan, radius, min_score)
    prim_t = net[prim]
    prim_wf = prim_t[(prim_t.index >= plan[0]["test_start"]) & (prim_t.index <= plan[-1]["test_end"])]
    wf_s, pw_s = (summarize(wf) if len(wf) else None), summarize(prim_wf)
    wf_mean, wf_se = _mean_se(wf["r_multiple"]) if len(wf) else (np.nan, np.nan)
    pw_mean, pw_se = _mean_se(prim_wf["r_multiple"])
    active = folds.loc[folds["cell"].notna(), "test_year"].astype(int).tolist()
    n_active = len(active)
    prim_active = prim_t[prim_t.index.year.isin(active)] if n_active else prim_t.iloc[0:0]
    pa_mean, pa_se = _mean_se(prim_active["r_multiple"]) if len(prim_active) > 1 else (np.nan, np.nan)
    fold_tbl = folds.copy()
    fold_tbl["test_year"] = fold_tbl["test_year"].astype(str)
    fold_tbl["cell"] = fold_tbl["cell"].map(fmt_cell)
    for c in ("train_smoothed_r", "train_raw_r", "test_r"):
        fold_tbl[c] = fold_tbl[c].map(lambda x: "n/a" if pd.isna(x) else f"{x:+.3f}")
    fold_tbl = fold_tbl.rename(columns={"train_smoothed_r": "train smoothed (R)", "train_raw_r": "train raw (R)", "test_r": "test (R)", "test_trades": "test trades"})
    def _row(tr, m, se):
        if len(tr) <= 1:
            return ["0", "n/a", "n/a", "0"]
        return [f"{len(tr):,}", f"{m:+.3f}", f"{m - 1.96 * se:+.3f} to {m + 1.96 * se:+.3f}", f"{tr['pnl_usd_nq'].sum():+,.0f}"]

    wf_tbl = pd.DataFrame(
        {
            f"Walk-forward rule ({n_active} of {len(folds)} years traded)": _row(wf, wf_mean, wf_se) if len(wf) else ["0", "n/a", "n/a", "0"],
            "Primary spec, same years the rule traded": _row(prim_active, pa_mean, pa_se),
            f"Primary spec, all {len(folds)} test years": _row(prim_wf, pw_mean, pw_se),
        },
        index=["Out-of-sample trades", "Expectancy (R)", "Naive 95% CI (R)", "Total $ (1 NQ)"],
    )
    wf_tbl.index.name = "1x costs"
    hind = best_raw
    notes = []
    if len(wf) and (wf_mean - 1.96 * wf_se) <= 0:
        notes.append(f"* The rule's out-of-sample result rests on only **{n_active} active fold(s)**; its interval includes zero, and a figure from so few years says more about those years than about the rule.")
    hind_mean = mean_net[grid.index(hind)]
    if len(wf) and wf_mean > hind_mean:
        notes.append(f"* The walk-forward figure is *higher* than the in-hindsight best in-sample cell ({hind_mean:+.3f} R). Selection optimism would push it the other way, so this is **not** an optimism gap: it reflects which years were tested.")
    elif len(wf):
        notes.append(f"* The walk-forward figure is below the in-hindsight best in-sample cell ({hind_mean:+.3f} R); the gap is the optimism of choosing the best of 96 after the fact.")
    wf_notes = "\n".join(notes)

    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    c1 = prim_wf["r_multiple"].cumsum()
    ax.plot(c1.index, c1.values, color=ORANGE, lw=2)
    ax.text(c1.index[-1], c1.values[-1], "  always primary", color=INK2, va="center", fontsize=9)
    if len(wf):
        c2 = wf.sort_index()["r_multiple"].cumsum()
        ax.plot(c2.index, c2.values, color=BLUE, lw=2)
        ax.text(c2.index[-1], c2.values[-1], "  walk-forward", color=INK2, va="center", fontsize=9)
    for _, f in folds[folds["cell"].isna()].iterrows():
        ax.axvspan(pd.Timestamp(f"{int(f['test_year'])}-01-01"), pd.Timestamp(f"{int(f['test_year'])}-12-31"), color="#e6e5e1", alpha=0.7, zorder=0)
    if folds["cell"].isna().any():
        ax.text(pd.Timestamp("2018-07-01"), ax.get_ylim()[1] * 0.9, "grey = rule sat out (no region with positive\nsmoothed expectancy in the training window)", ha="center", va="top", fontsize=8, color=INK2)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_ylabel("Cumulative net R (out-of-sample only)")
    ax.set_title("Walk-forward out-of-sample, 2015-2023, 1x costs")
    ax.margins(x=0.12)
    fig.tight_layout(); fig.savefig(out / "fig13_walk_forward.png", dpi=140); plt.close(fig)

    # ---- roll-day scenario (same variant, not a trial) ----
    roll_only = daily.index[daily["exclude_reason"].isin(["roll_new_contract", "roll_low_share"])]
    orb, ex = params_from_cfg(cfg), exec_params_from_cfg(cfg)

    def _prim_on(ds):
        sig = generate_signals(bars, ds, orb, tz=cfg.session.timezone)
        tr, _ = simulate(bars, sig, orb, ex, tz=cfg.session.timezone)
        return tr

    inc = _prim_on(dates.union(roll_only))
    ro = inc[inc.index.isin(roll_only)]
    roll_rows = []
    for label, tr in (("Roll days excluded (primary)", prim_t), ("Roll days included", inc), ("Roll days only", ro)):
        m, se = _mean_se(tr["r_multiple"])
        roll_rows.append((label, f"{len(tr):,}", f"{m:+.3f}", f"{m - 1.96 * se:+.3f} to {m + 1.96 * se:+.3f}", f"{tr['pnl_usd_nq'].sum():+,.0f}"))
    roll_tbl = pd.DataFrame(roll_rows, columns=["Sample (primary spec, 1x costs)", "trades", "expectancy (R)", "naive 95% CI (R)", "$ (1 NQ)"])

    # ---- promotion verdict (D4, mechanical) ----
    a = sel_full is not None
    b = bool(len(wf)) and wf_mean > 0
    c_ = is_cell is not None and np.isfinite(oos_sel) and oos_sel > 0
    promoted = sel_full if (a and b and c_) else None
    verdict = pd.DataFrame(
        [
            ("(a) Rule selects a cell on the full development period (smoothed score above 0 at 1x)", "PASS" if a else "FAIL",
             f"{fmt_cell(sel_full)}" + (f", smoothed {sm_full[sel_idx]:+.3f} R" if a else f"; best smoothed score {np.nanmax(sm_full):+.3f} R")),
            ("(b) Walk-forward out-of-sample net expectancy above 0", "PASS" if b else "FAIL", (f"{wf_mean:+.3f} R on {n_active} active fold(s) of {len(folds)}; naive 95% CI {wf_mean - 1.96 * wf_se:+.3f} to {wf_mean + 1.96 * wf_se:+.3f}") if len(wf) else "no trades (rule sat out every fold)"),
            ("(c) Rule-selected cell has OOS expectancy above 0 on the single split", "PASS" if c_ else "FAIL",
             f"{oos_sel:+.3f} R ({fmt_cell(is_cell)})" if is_cell is not None else "rule selected nothing on the in-sample window"),
        ],
        columns=["Criterion (D4)", "Result", "Detail"],
    )
    prim_t_stat = float(t_stat[pi])
    results_csv = pd.DataFrame(
        [{"range_minutes": c[0], "stop_fraction": c[1], "target_r": "none" if c[2] is None else c[2],
          "trades": int(full["n"][grid.index(c)]), "net_expectancy_r": mean_net[grid.index(c)], "gross_expectancy_r": full_g["mean_r"][grid.index(c)],
          "net_t_naive": t_stat[grid.index(c)], "smoothed_net_r": sm_full[grid.index(c)],
          "is_expectancy_r": is_s["mean_r"][grid.index(c)], "oos_expectancy_r": oos_s["mean_r"][grid.index(c)]} for c in grid.cells]
    )
    results_csv.to_csv(out / "grid_results.csv", index=False, float_format="%.5f")
    folds.assign(cell=folds["cell"].map(fmt_cell)).to_csv(out / "walk_forward_folds.csv", index=False, float_format="%.5f")

    md = f"""# Validation report (Stage 6)

Development data only ({d0.date()} to {d1.date()}); the 2024+ holdout was **not** loaded. This report executes, without deviation, the protocol that was
written and committed **before any result existed** (`docs/DECISIONS.md`, D4, git commit `57bc384`). All 96 grid cells are recorded as trials
(registry total now **{count_trials(cfg)}**). Costs are 1x unless stated. Full numbers: `grid_results.csv`, `walk_forward_folds.csv`.

## 1. The whole grid (not just the good cells)

* Cells with positive **net** expectancy: **{pos_net:.0%}** ({int(round(pos_net * 96))} of 96). With **zero** costs: **{pos_gross:.0%}** ({int(round(pos_gross * 96))} of 96).
* Median net expectancy across the grid: **{np.nanmedian(mean_net):+.3f} R**; range {np.nanmin(mean_net):+.3f} to {np.nanmax(mean_net):+.3f} R.
* The primary spec's cell: **{mean_net[pi]:+.3f} R** (naive t = {prim_t_stat:+.2f}).
* Best raw cell: {fmt_cell(best_raw)} at {hind_mean:+.3f} R (naive t = {t_stat[grid.index(best_raw)]:+.2f}). For scale: if the 96 cells were *independent noise*, the best naive t-statistic
  would be about **{null_max:.2f}** on average (cells here are strongly correlated, so the true null maximum is lower). {"The best cell is **below** that level: even before any correction it is not unusual for the best of 96 noise draws." if t_stat[grid.index(best_raw)] < null_max else "The best cell is at or above that level, which makes the correction below important."}
  Stage 7 does the proper deflated-Sharpe / reality-check correction.

Where the cost drag falls (frictionless minus net expectancy, in R): costs are about the same dollars per trade, but R (the risk unit) is small for short ranges and tight stops, so those cells
give up the most:

{md_table(drag_tbl)}

The frictionless map (second figure) shows a cluster of positive cells for **no target + tight stop + short range** (up to {np.nanmax(full_g['mean_r']):+.2f} R). Most of it is consumed by costs. Note that "no target"
means holding to the time exit at {cfg.session.flat_time}, so that cluster may simply be riding NQ's strong 2010-2023 drift rather than breakout behaviour: it is a hypothesis for Stage 7
(split long vs short, compare with a same-hold-time benchmark that ignores the breakout), logged as D5, not a finding.

![net heatmap](fig10_heatmap_net.png)

![gross heatmap](fig11_heatmap_gross.png)

Top and bottom five cells, for transparency (**not** used for selection):

{md_table(top, index=False)}

{md_table(bottom, index=False)}

## 2. Selection by the pre-registered rule (smoothed, sit out if no edge)

* Full development period: **{fmt_cell(sel_full)}**{f" with a smoothed score of {sm_full[sel_idx]:+.3f} R" if a else f" (best smoothed score was {np.nanmax(sm_full):+.3f} R, not above {min_score:g})"}.

## 3. One in-sample / out-of-sample split ({v.is_oos.is_start[:4]}-{v.is_oos.is_end[:4]} train, {v.is_oos.oos_start[:4]}-{v.is_oos.oos_end[:4]} test)

{md_table(isoos_tbl, index=False)}

* The average cell scored **{is_grid_mean:+.3f} R** in-sample and **{oos_grid_mean_all:+.3f} R** out-of-sample; the share of positive cells went from {is_pos:.0%} to {oos_pos:.0%}.
  The whole grid moving up together is a **period effect**: 2019-2023 was a kinder period for this strategy family than 2010-2018 whatever the parameters (this is the
  post-hoc regime hypothesis, D3, again; nothing here explains it).
* Across all 96 cells, in-sample and out-of-sample expectancy have rank correlation **{rho:+.2f}**. Read this carefully: the 96 cells share the same days and largely the same trades, so they are **not
  independent** points and no p-value is reported. A positive rank correlation says the *ordering* of exit structures persisted (for example how costs bite on small-R cells, see section 1), not
  that any cell earns money.
* The ten best cells in-sample averaged **{top10_oos:+.3f} R** out-of-sample, against **{grid_oos_mean:+.3f} R** for the average cell.

![is vs oos](fig12_is_vs_oos.png)

## 4. Walk-forward (train 5 years, test the next year; test years {plan[0]['test_year']}-{plan[-1]['test_year']})

{md_table(fold_tbl, index=False)}

{md_table(wf_tbl)}

* **The rule sat out {len(folds) - n_active} of {len(folds)} folds**: in those training windows no neighbourhood of the grid had positive smoothed net expectancy. It traded only in: {', '.join(map(str, active)) or 'no year'}.
  The fair comparison is therefore the first two columns (the same years), not the third.
{wf_notes}

![walk forward](fig13_walk_forward.png)

## 5. Contract rolls: included vs excluded (same variant, a scenario not a trial)

{md_table(roll_tbl, index=False)}

Roll-day trades are a small slice; if including them changes the answer materially the "exclude rolls" choice would matter. Compare the expectancy columns and their widths.

## 6. Promotion verdict (D4, computed mechanically)

{md_table(verdict, index=False)}

**Holdout candidates: the primary specification (always){f", plus {fmt_cell(promoted)}" if promoted else ""}.**
{"All three criteria passed. This earns the variant a place on the holdout; it is not a claim of edge." if promoted else "At least one criterion failed, so no variant is promoted: only the primary specification will be run on the holdout (D1)."}

Note on criterion (b): it was pre-registered as "stitched walk-forward expectancy above zero" and is applied exactly as written. As specified it can pass on very few active folds, a weakness of the criterion
(it did not decide the outcome here because (a) failed). The rule is **not** changed after seeing results; the strength of the evidence is judged by the Stage 7 statistics.

## 7. What could invalidate this

* 96 cells on one 13.5-year history: a fortunate cell is expected by chance (section 1). Selection used smoothing precisely to avoid chasing it, but smoothing cannot create information that is not there.
* Cells are strongly correlated (they share the same signals and days), so "96" overstates the number of independent bets while also making naive intervals too narrow.
* R-expectancy is not strictly comparable across stop fractions (R is defined by the stop distance); it measures return per unit of risk taken in that cell.
* The grid and rule were fixed in advance, but the grid itself came from a researcher's idea of what is worth testing; the true number of ideas tried is not zero beyond this.
* Costs are assumptions (1 tick, $2/side); a cell that is positive at 1x may not be at 2x. Frictionless and 1x heatmaps are shown so the cost sensitivity is visible.
"""
    path = out / "validation_report.md"
    path.write_text(md, encoding="utf-8")
    return str(path)
