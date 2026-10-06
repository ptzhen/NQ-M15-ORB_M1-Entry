"""Stage 5 report: the backtest on development data, with the trade log and the standard breakdowns.

Descriptive: it shows what happened, with naive uncertainty. It does not select or tune anything.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from orb.analysis.backtest import export_trade_log, run_backtest
from orb.analysis.metrics import drawdown_stats, group_stats, summarize
from orb.analysis.trials import count_trials, record_trial
from orb.config import resolve
from orb.reports.data_quality import BLUE, INK2, MUTED_BAR, SURFACE, _style, md_table


def _fmt_group(g: pd.DataFrame, index_name: str) -> pd.DataFrame:
    out = pd.DataFrame({
        "trades": g["trades"].astype(int).map("{:,}".format),
        "win rate": g["win_rate"].map("{:.1%}".format),
        "expectancy (R)": g["expectancy_r"].map("{:+.3f}".format),
        "naive 95% CI (R)": [f"{m - 1.96 * s:+.3f} to {m + 1.96 * s:+.3f}" if pd.notna(s) else "n/a" for m, s in zip(g["expectancy_r"], g["se_r"])],
        "profit factor": g["profit_factor"].map("{:.2f}".format),
        "net pts": g["total_points"].map("{:+,.0f}".format),
        "$ (1 NQ)": g["total_usd_nq"].map("{:+,.0f}".format),
        "$ (1 MNQ)": g["total_usd_mnq"].map("{:+,.0f}".format),
    })
    out.index = out.index.astype(str)
    out.index.name = index_name
    return out


def build_backtest_report(cfg) -> str:
    out = resolve(cfg, cfg.paths.output_dir) / "backtest"
    out.mkdir(parents=True, exist_ok=True)
    _style()
    res = run_backtest(cfg, "dev", purpose="stage 5 backtest report (development data, primary spec, no tuning)")
    t, orb, ex = res.trades, res.orb, res.ex
    pv = cfg.data.point_value_usd
    export_trade_log(t, out / "trade_log_dev.csv")

    trial_params = {"range_minutes": orb.range_minutes, "stop_range_fraction": ex.stop_fraction, "target_r": ex.target_r,
                    "target_fill": ex.target_fill, "ambiguity": ex.ambiguity, "last_entry_bar": cfg.signal.last_entry_bar}
    record_trial(cfg, "stage4", "primary_spec", trial_params)      # same key as Stage 4: no double counting

    s = summarize(t)
    is_win = t["pnl_usd_nq"] > 0
    wins, losses = t.loc[is_win, "r_multiple"], t.loc[~is_win, "r_multiple"]
    loss_streak = int((~is_win).astype(int).groupby(is_win.cumsum()).cumsum().max())
    dd_usd, dd_r, dd_mnq = drawdown_stats(t["pnl_usd_nq"]), drawdown_stats(t["r_multiple"]), drawdown_stats(t["pnl_usd_mnq"])
    n_days = len(res.signals)
    exposure = t["minutes_in_trade"].sum() / (n_days * 390)
    gross_pts, slip_pts = t["gross_points"].sum(), t["slippage_points"].sum()
    comm_nq, comm_mnq = t["commission_usd_nq"].sum(), t["commission_usd_mnq"].sum()

    head = pd.DataFrame(
        [
            ("Trades (long / short)", f"{len(t):,}  ({(t['direction'] > 0).sum():,} / {(t['direction'] < 0).sum():,})"),
            ("Win rate", f"{s['win_rate']:.1%}"),
            ("Average winner / loser (R)", f"{wins.mean():+.2f} / {losses.mean():+.2f}"),
            ("Expectancy per trade", f"{s['expectancy_r']:+.3f} R  =  {s['avg_net_points']:+.2f} pts"),
            ("Profit factor", f"{s['profit_factor']:.2f}"),
            ("Longest losing streak", f"{loss_streak} trades"),
            ("Exposure (time in market / session time)", f"{exposure:.1%}  (avg {t['minutes_in_trade'].mean():.0f} min per trade)"),
            ("Max drawdown, 1 NQ / 1 MNQ / in R", f"${dd_usd['max_drawdown']:,.0f} / ${dd_mnq['max_drawdown']:,.0f} / {dd_r['max_drawdown']:.1f} R"),
            ("Longest time under water", f"{dd_usd['longest_underwater_days']:,} calendar days ({dd_usd['longest_underwater_days'] / 365.25:.1f} years)"),
        ],
        columns=["Metric (1x costs)", "Value"],
    )
    pnl = pd.DataFrame(
        {
            "NQ ($20/pt)": [f"{gross_pts:+,.1f}", f"{-slip_pts:+,.1f}", f"{-comm_nq / pv.NQ:+,.1f}", f"{t['net_points_nq'].sum():+,.1f}",
                            f"{gross_pts * pv.NQ:+,.0f}", f"{-slip_pts * pv.NQ:+,.0f}", f"{-comm_nq:+,.0f}", f"{t['pnl_usd_nq'].sum():+,.0f}"],
            "MNQ ($2/pt)": [f"{gross_pts:+,.1f}", f"{-slip_pts:+,.1f}", f"{-comm_mnq / pv.MNQ:+,.1f}", f"{gross_pts - slip_pts - comm_mnq / pv.MNQ:+,.1f}",
                            f"{gross_pts * pv.MNQ:+,.0f}", f"{-slip_pts * pv.MNQ:+,.0f}", f"{-comm_mnq:+,.0f}", f"{t['pnl_usd_mnq'].sum():+,.0f}"],
        },
        index=["Gross points (frictionless)", "Slippage (points)", "Commission (points)", "NET POINTS",
               "Gross $", "Slippage $", "Commission $", "NET $"],
    )
    pnl.index.name = "Whole development period, 1 contract"

    by_year = group_stats(t, "year")
    by_year.index = [f"{y}*" if y == 2010 else str(y) for y in by_year.index]
    by_regime = group_stats(t, "regime").reindex([r["name"] for r in cfg.regimes if r["name"] in set(t["regime"])])
    by_wd, by_side, by_reason = group_stats(t, "weekday"), group_stats(t, "side"), group_stats(t, "exit_reason")

    # ---- figures ----
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8.5, 6.0), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    a1.plot(dd_usd["equity"].index, dd_usd["equity"].values, color=BLUE, lw=1.8)
    a1.axhline(0, color=INK2, lw=0.8)
    a1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    a2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
    a1.set_ylabel("Cumulative net $ (1 NQ)")
    a1.set_title("Development period at 1x costs: equity and drawdown")
    a2.fill_between(dd_usd["drawdown"].index, -dd_usd["drawdown"].values, 0, color=MUTED_BAR, step="post")
    a2.set_ylabel("Drawdown $")
    fig.tight_layout(); fig.savefig(out / "fig8_equity_drawdown.png", dpi=140); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    x = np.arange(len(by_year))
    ax.bar(x, by_year["expectancy_r"], color=BLUE, width=0.65, edgecolor=SURFACE, linewidth=2)
    ax.errorbar(x, by_year["expectancy_r"], yerr=1.96 * by_year["se_r"], fmt="none", ecolor=INK2, elinewidth=1.2, capsize=3)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xticks(x, by_year.index, fontsize=9)
    ax.set_ylabel("Expectancy (R per trade)")
    ax.set_title("Expectancy by year with naive 95% intervals  (* partial year)")
    ax.grid(axis="x", visible=False)
    fig.tight_layout(); fig.savefig(out / "fig9_expectancy_by_year.png", dpi=140); plt.close(fig)

    reason_tbl = pd.DataFrame({
        "trades": by_reason["trades"].astype(int).map("{:,}".format),
        "share": (by_reason["trades"] / by_reason["trades"].sum()).map("{:.1%}".format),
        "avg R": by_reason["expectancy_r"].map("{:+.3f}".format),
        "$ (1 NQ)": by_reason["total_usd_nq"].map("{:+,.0f}".format),
    })
    reason_tbl.index.name = "exit reason"

    n_pos = int((by_year["expectancy_r"] > 0).sum())
    n_sig = int((by_year["expectancy_r"] - 1.96 * by_year["se_r"] > 0).sum())
    first, last = t.index.min().date(), t.index.max().date()
    md = f"""# Backtest report (Stage 5)

Development data only ({first} to {last}). The 2024+ holdout was **not** loaded. Specification is the brief's and is not tuned:
{orb.range_minutes}-minute range, stop at the opposite side of the range, {ex.target_r:g}R target, max one trade a day, flat by {cfg.session.flat_time},
one contract, costs at 1x (${cfg.execution.costs.commission_per_side_usd.NQ:.2f}/side NQ, ${cfg.execution.costs.commission_per_side_usd.MNQ:.2f}/side MNQ,
{cfg.execution.costs.slippage_ticks:g} tick slippage on market-type fills). Trials registered so far: **{count_trials(cfg)}**.

The full trade log (every entry, exit, reason, R-multiple, and costs) is `outputs/backtest/trade_log_dev.csv` (local; not committed
because it contains market prices from licensed data). Rebuild it with `python -m orb backtest`.

## 1. Headline

{md_table(head, index=False)}

## 2. P&L in points and dollars (NQ and MNQ)

{md_table(pnl)}

Points are identical for NQ and MNQ (same index); dollars differ by the multiplier, and MNQ's commission is a bigger share of its P&L.
"Net points" for NQ and MNQ differ only because the fixed commission converts to a different number of points.

![equity](fig8_equity_drawdown.png)

Dollar curves for a fixed one contract are dominated by the later years: NQ traded near 1,800 in 2010 and above 15,000 by 2023, so a one-contract
point is worth far more relative to price now, and ranges (hence R in dollars) are far larger. Compare eras in R, not dollars (section 4).

## 3. By exit reason

{md_table(reason_tbl)}

Exit reason is decided by the outcome itself (a stop exit is a loser by construction), so no win rate or confidence interval is shown here:
that would be circular. Time exits are the only group where the result was not predetermined.

## 4. By year

{md_table(_fmt_group(by_year, "year"))}

{n_pos} of {len(by_year)} years have positive expectancy; {n_sig} have a naive 95% interval entirely above zero. With ~{int(by_year['trades'].mean())} trades a year
and a standard error near {by_year['se_r'].mean():.2f} R, a single year cannot distinguish a real +0.05R edge from noise. 2010 is a partial year. The 2024-2026 years are in the holdout and are not shown.

![by year](fig9_expectancy_by_year.png)

## 5. By regime (development data only)

{md_table(_fmt_group(by_regime, "regime"))}

"2023 onward" contains only 2023 until the holdout is run. 2021 is a bucket we added (the brief lists the other four).

## 6. By weekday and by side

{md_table(_fmt_group(by_wd, "weekday"))}

{md_table(_fmt_group(by_side, "side"))}

These cuts are descriptive. Slicing results many ways and then reading meaning into the best slice is a classic way to fool yourself: each cut is a
chance for a false positive, and none of them is used to select anything.

## 7. Post-hoc observation (a hypothesis to test, not a finding)

Looking at section 4, 2015-2017 are clearly negative while 2019 and 2021-2023 are around zero or slightly positive. A natural idea is "the strategy does better
in higher-volatility regimes". This was noticed **after** seeing the results, so it must not be treated as evidence: with 14 years and 5 regimes, some pattern
is always visible. It is logged in `docs/DECISIONS.md` (D3) and will be tested only with a volatility-regime definition fixed in advance (Stage 7), counted as
a trial, and judged again on the untouched holdout.

## 8. What could invalidate this

* All the cost and fill caveats of Stage 4 apply (slippage and limit-fill assumptions).
* Naive intervals ignore clustering of outcomes by volatility regime, so they are too narrow.
* Weekday/side/regime cuts split ~{len(t):,} trades into small groups; apparent differences are mostly noise unless they are large and consistent.
"""
    path = out / "backtest_report.md"
    path.write_text(md, encoding="utf-8")
    return str(path)
