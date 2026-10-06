"""Stage 8 report: the final write-up. The conclusion text is composed mechanically from the D8 verdict."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from orb.analysis.metrics import drawdown_stats
from orb.analysis.robustness import bootstrap_stat, mean_rows, percentile_ci
from orb.analysis.trials import count_trials
from orb.config import resolve
from orb.reports.data_quality import BLUE, INK2, MUTED_BAR, ORANGE, SURFACE, _style, md_table
from orb.analysis.final import Period


def _iv(t: tuple, fmt: str = "{:+.3f}") -> str:
    return f"{fmt.format(t[0])}  [{fmt.format(t[1])} to {fmt.format(t[2])}]"


def _pct(t: tuple) -> str:
    return f"{t[0]:.1%}  [{t[1]:.1%} to {t[2]:.1%}]"


def _span(p: Period) -> str:
    return f"{p.dates.min().date()} to {p.dates.max().date()}"


def render_final(cfg, res: dict, out_dir, mode: str) -> str:
    """mode: 'FINAL', 'REPRODUCTION' or 'DRY RUN'."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _style()
    dev, ho, vol, dirn, vd = res["dev"], res["ho"], res["vol"], res["dir"], res["verdict"]
    s_dev, s_ho = dev.stats, ho.stats
    B, L = cfg.robustness.bootstrap.resamples, cfg.robustness.bootstrap.mean_block_days
    pvm = cfg.data.point_value_usd
    dry = mode == "DRY RUN"

    # ------------------------------------------------------------------ figures
    t_all = pd.concat([dev.trades[1.0], ho.trades[1.0]])
    eq = drawdown_stats(t_all["r_multiple"])
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8.5, 6.2), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    d_end = dev.trades[1.0].index.max()
    cum = eq["equity"]
    a1.plot(cum[cum.index <= d_end].index, cum[cum.index <= d_end].values, color=BLUE, lw=1.8, label="Development")
    a1.plot(cum[cum.index >= ho.trades[1.0].index.min()].index, cum[cum.index >= ho.trades[1.0].index.min()].values, color=ORANGE, lw=1.8, label="Holdout")
    a1.axhline(0, color=INK2, lw=0.8)
    a1.axvline(res["split"], color=INK2, lw=1, ls="--")
    a1.set_ylabel("Cumulative net R (1x costs)")
    a1.set_title("Primary specification: development and holdout" + ("  [DRY RUN, pseudo-holdout]" if dry else ""))
    a1.legend(frameon=False, loc="lower left")
    a2.fill_between(eq["drawdown"].index, -eq["drawdown"].values, 0, color=MUTED_BAR, step="post")
    a2.axvline(res["split"], color=INK2, lw=1, ls="--")
    a2.set_ylabel("Drawdown (R)")
    fig.tight_layout(); fig.savefig(out_dir / "fig18_final_equity.png", dpi=140); plt.close(fig)

    ys = pd.concat([dev.year_stats.assign(period="Development"), ho.year_stats.assign(period="Holdout")], ignore_index=True)
    ys["partial"] = (ys["last_date"] - ys["first_date"]).dt.days < 330
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    x = np.arange(len(ys))
    ax.bar(x, ys["mean_r"], color=[BLUE if p == "Development" else ORANGE for p in ys["period"]], width=0.65, edgecolor=SURFACE, linewidth=2)
    ax.errorbar(x, ys["mean_r"], yerr=[ys["mean_r"] - ys["lo"], ys["hi"] - ys["mean_r"]], fmt="none", ecolor=INK2, elinewidth=1.2, capsize=3)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xticks(x, [f"{y}{'*' if p else ''}" for y, p in zip(ys["year"], ys["partial"])], fontsize=8, rotation=0)
    ax.set_ylabel("Expectancy (R per trade)")
    ax.set_title("By year, 95% block-bootstrap intervals  (blue = development, orange = holdout, * = partial year)", fontsize=10)
    ax.grid(axis="x", visible=False)
    fig.tight_layout(); fig.savefig(out_dir / "fig19_final_by_year.png", dpi=140); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    rows = [(f"{p.label.capitalize()}  {sc:g}x costs", p.cost_stats[sc], c) for p, c in ((dev, BLUE), (ho, ORANGE)) for sc in (0.0, 1.0, 2.0)]
    yp = np.arange(len(rows))[::-1]
    for y, (lab, cs, c) in zip(yp, rows):
        ax.hlines(y, cs["lo"], cs["hi"], color=INK2, lw=1.6)
        ax.scatter([cs["mean_r"]], [y], color=c, s=44, zorder=3, edgecolor=SURFACE, linewidth=1.2)
    ax.axvline(0, color=INK2, lw=1)
    ax.set_yticks(yp, [r[0] for r in rows], fontsize=9)
    ax.set_xlabel("Expectancy (R per trade), 95% block-bootstrap interval")
    ax.set_title("Same trades at 0x, 1x and 2x assumed costs")
    ax.grid(axis="y", visible=False)
    fig.tight_layout(); fig.savefig(out_dir / "fig20_final_cost_scenarios.png", dpi=140); plt.close(fig)

    # ------------------------------------------------------------------ tables
    def col(s: dict, p: Period) -> list:
        return [
            _span(p), f"{s['days']:,}", f"{s['trades']:,}  ({s['longs']:,} long / {s['shorts']:,} short)",
            _pct(s["win_rate"]), f"{s['avg_win_r']:+.2f} / {s['avg_loss_r']:+.2f}", _iv(s["expectancy_r"]),
            f"{s['p_expectancy_positive']:.1%}", _iv(s["profit_factor"], "{:.2f}"), _iv(s["sharpe"], "{:+.2f}"), _iv(s["sortino"], "{:+.2f}"),
            f"{s['net_points']:+,.0f}  ({s['avg_net_points']:+.2f} per trade)", f"{s['total_usd_nq']:+,.0f} / {s['total_usd_mnq']:+,.0f}",
            f"${s['max_dd_usd_nq']:,.0f} / ${s['max_dd_usd_mnq']:,.0f} / {s['max_dd_r']:.1f} R",
            f"{s['longest_underwater_days']:,} days ({s['longest_underwater_days'] / 365.25:.1f} y)", f"{s['exposure']:.1%}  (avg {s['avg_minutes']:.0f} min)", f"{s['longest_loss_streak']} trades",
        ]
    labels = ["Period", "Tradable days", "Trades", "Win rate  [95% interval]", "Average winner / loser (R)", "Expectancy (R)  [95% interval]", "Bootstrap share of resamples with expectancy > 0",
              "Profit factor  [95% interval]", "Sharpe, annualised  [95% interval]", "Sortino, annualised  [95% interval]", "Net points (NQ costs)", "Net $ for 1 NQ / 1 MNQ",
              "Max drawdown: 1 NQ / 1 MNQ / in R", "Longest time under water", "Exposure (time in market)", "Longest losing streak"]
    head = pd.DataFrame({"Development": col(s_dev, dev), "Holdout (untouched until the end)" if not dry else "PSEUDO-holdout": col(s_ho, ho)}, index=labels)
    head.index.name = "Primary specification, 1x costs"

    cost_rows = []
    for sc in (0.0, 1.0, 2.0):
        for p in (dev, ho):
            cs = p.cost_stats[sc]
            cost_rows.append((f"{sc:g}x", p.label, f"{cs['trades']:,}", f"{cs['mean_r']:+.3f}  [{cs['lo']:+.3f} to {cs['hi']:+.3f}]", f"{cs['total_usd_nq']:+,.0f}", f"{cs['total_usd_mnq']:+,.0f}"))
    cost_tbl = pd.DataFrame(cost_rows, columns=["cost level", "period", "trades", "expectancy (R) [95% interval]", "$ (1 NQ)", "$ (1 MNQ)"])

    def yr_tbl(p: Period) -> pd.DataFrame:
        d = p.year_stats.copy()
        d["year"] = [f"{y}{'*' if (l - f).days < 330 else ''}" for y, f, l in zip(d["year"], d["first_date"], d["last_date"])]
        d["trades"] = d["trades"].map("{:,}".format)
        d["win rate"] = d["win_rate"].map("{:.1%}".format)
        d["expectancy (R) [95% interval]"] = [f"{m:+.3f}  [{a:+.3f} to {b:+.3f}]" for m, a, b in zip(d["mean_r"], d["lo"], d["hi"])]
        d["$ (1 NQ)"] = d["total_usd_nq"].map("{:+,.0f}".format)
        d["$ (1 MNQ)"] = d["total_usd_mnq"].map("{:+,.0f}".format)
        return d[["year", "trades", "win rate", "expectancy (R) [95% interval]", "$ (1 NQ)", "$ (1 MNQ)"]]

    reg_rows = []
    for r in cfg.regimes:
        sub = t_all[t_all["regime"] == r["name"]]
        if len(sub) < 2:
            continue
        lo, hi = percentile_ci(bootstrap_stat(sub["r_multiple"].to_numpy(), mean_rows, B, L, np.random.default_rng(cfg.seed + 500)))
        split_note = " (development + holdout)" if (sub.index >= res["split"]).any() and (sub.index < res["split"]).any() else (" (holdout only)" if (sub.index >= res["split"]).all() else "")
        reg_rows.append((r["name"] + split_note, f"{len(sub):,}", f"{(sub['r_multiple'] > 0).mean():.1%}", f"{sub['r_multiple'].mean():+.3f}  [{lo:+.3f} to {hi:+.3f}]", f"{sub['pnl_usd_nq'].sum():+,.0f}", f"{sub['pnl_usd_mnq'].sum():+,.0f}"))
    reg_tbl = pd.DataFrame(reg_rows, columns=["regime", "trades", "win rate", "expectancy (R) [95% interval]", "$ (1 NQ)", "$ (1 MNQ)"])

    side_rows = []
    for p in (dev, ho):
        for sd in ("long", "short"):
            sub = p.trades[1.0][p.trades[1.0]["side"] == sd]
            if len(sub) < 2:
                continue
            lo, hi = percentile_ci(bootstrap_stat(sub["r_multiple"].to_numpy(), mean_rows, B, L, np.random.default_rng(cfg.seed + 600)))
            side_rows.append((p.label, sd, f"{len(sub):,}", f"{sub['r_multiple'].mean():+.3f}  [{lo:+.3f} to {hi:+.3f}]", f"{sub['pnl_usd_nq'].sum():+,.0f}"))
    side_tbl = pd.DataFrame(side_rows, columns=["period", "side", "trades", "expectancy (R) [95% interval]", "$ (1 NQ)"])

    hv = vol["high"]
    tests = pd.DataFrame(
        [
            ("(i) Primary spec, 1x costs", "interval for expectancy entirely above 0 = positive; entirely below = negative", f"{_iv(s_ho['expectancy_r'])}", vd["primary"]),
            ("(ii) Primary spec, high-volatility tercile only (D3)", "high-tercile interval entirely above 0 (needs >= 20 trades)",
             (f"{hv['n']:,} trades: {hv['mean_r']:+.3f}  [{hv['lo']:+.3f} to {hv['hi']:+.3f}]" if vol["testable"] else f"only {hv['n']} trades"),
             "SUPPORTED" if vol["supported"] else ("untestable" if not vol["testable"] else "NOT supported")),
            ("(iii) Breakout direction vs drift (D5)", "permutation p-value < 0.05 (Bonferroni for 3 tests: < 0.0167)",
             f"signal-direction hold-to-close {dirn['observed_mean_bps']:+.2f} bps vs {dirn['null_mean_bps']:+.2f} +/- {dirn['null_sd_bps']:.2f} if random; p = {dirn['p_value']:.3f}",
             "SUPPORTED" if dirn["supported"] else "NOT supported"),
        ],
        columns=["Declared test (D6/D8)", "Rule", "Result", "Outcome"],
    )

    # ------------------------------------------------------------------ plain-English conclusion
    e_dev, e_ho = s_dev["expectancy_r"], s_ho["expectancy_r"]
    ho_word = {"negative and distinguishable": "it lost money again, and the loss is large enough to be distinguishable from zero",
               "positive and distinguishable": "it made money, and the gain is large enough to be distinguishable from zero",
               "not distinguishable from zero": "the result is statistically indistinguishable from zero (the interval spans zero)"}[vd["primary"]]
    if vd["edge"]:
        headline = "**Evidence of a real, tradable edge (by the pre-declared rules).**"
        verdict_text = ("All three of the pre-declared conditions held: the holdout interval is entirely above zero at 1x costs, the holdout is still positive at 2x costs, and the development "
                        "result did not contradict it. This is the strongest conclusion the rules allow, and it still rests on a short, single history.")
    else:
        headline = "**No edge established.**"
        verdict_text = "The pre-declared conditions for claiming an edge were not all met. Failed: " + "; ".join(vd["failed"]) + "."
    mixed = vd["primary"] == "positive and distinguishable" and not vd["edge"]
    sh, pf, c2 = s_ho["sharpe"], s_ho["profit_factor"], ho.cost_stats[2.0]
    ho_t = ho.trades[1.0]
    side_ho = {sd: ho_t.loc[ho_t["side"] == sd, "r_multiple"].mean() for sd in ("long", "short")}
    p1 = 1.0 - s_ho["p_expectancy_positive"]
    ctx = []
    if mixed:
        ctx.append(f"* **How strong is the holdout gain?** Its interval is {e_ho[1]:+.3f} to {e_ho[2]:+.3f} R, so the lower end is only {e_ho[1]:+.3f} R: the result clears zero narrowly. The one-sided bootstrap p-value is about {p1:.3f} "
                   f"({'below' if p1 < 0.05 / 3 else 'not below'} the Bonferroni level of {0.05 / 3:.4f} for three declared tests). The Sharpe interval is {sh[1]:+.2f} to {sh[2]:+.2f} and the profit-factor interval {pf[1]:.2f} to {pf[2]:.2f}"
                   f"{' (both include the no-edge values)' if sh[1] <= 0 and pf[1] <= 1 else ''}; at 2x costs the expectancy interval is {c2['lo']:+.3f} to {c2['hi']:+.3f} R.")
        ctx.append(f"* **Is it the breakout, or the market?** The direction test did not support breakout information (p = {dirn['p_value']:.3f}); over the same entry-to-close windows the market itself moved {dirn['always_long_mean_bps']:+.2f} bps. "
                   f"In the holdout the long trades averaged {side_ho['long']:+.3f} R and the short trades {side_ho['short']:+.3f} R. A gain that comes mainly from the long side while NQ is rising is what market drift would also produce.")
        ctx.append(f"* **It reverses the earlier history.** Development was {e_dev[0]:+.3f} R per trade (interval {e_dev[1]:+.3f} to {e_dev[2]:+.3f}); the holdout is {e_ho[0]:+.3f} R. The rule's performance clearly is not stable over time, and the one regime explanation "
                   f"we tested in advance (prior volatility) does not account for it (test (ii) in section 4).")
    ctx_text = "\n".join(ctx)
    if vd["edge"]:
        plain = "the evidence favours a small positive expectancy, but it should be confirmed on fresh data before any money depends on it."
    elif mixed:
        plain = ("the evidence is **mixed**. For 13.5 years this rule lost money after costs; the later, untouched data show a gain that is statistically positive but only just, that is not shown to come from the breakout "
                 "itself, and that contradicts the earlier history. By the rules we set in advance that is **not** enough to call it an edge. It is a reason to keep observing the rule on genuinely new data, not a reason to expect profits.")
    else:
        plain = "a trader following this rule would, on this evidence, have no reason to expect to make money after costs."
    conclusion = f"""{headline}

* **What was tested.** The textbook 15-minute opening range breakout on NQ: after the first 15 minutes of the US session, go long (short) when a 1-minute bar closes above (below) the range, stop at the other side of the range, target 1x the risk, flat by 15:55, one trade a day.
* **Development, {_span(dev)} ({s_dev['trades']:,} trades).** After assumed costs it lost {abs(e_dev[0]):.3f} R per trade on average (95% interval {e_dev[1]:+.3f} to {e_dev[2]:+.3f}); about {s_dev['total_usd_nq']:+,.0f} dollars for one NQ contract.
  Before any cost it was {dev.cost_stats[0.0]['mean_r']:+.3f} R, indistinguishable from zero. In the full development period (Stages 6-7), across 96 parameter combinations (range length, stop, target) only 5 were positive after costs, and after correcting for having tried them all, none was distinguishable from luck.
* **{'Pseudo-holdout' if dry else 'Holdout'}, {_span(ho)} ({s_ho['trades']:,} trades).** On {'data inside the development period (dry run)' if dry else 'data that no decision had ever seen'}, {ho_word}: {e_ho[0]:+.3f} R per trade (95% interval {e_ho[1]:+.3f} to {e_ho[2]:+.3f}), {s_ho['total_usd_nq']:+,.0f} dollars for one NQ. At 2x costs: {ho.cost_stats[2.0]['mean_r']:+.3f} R.
* **Two ideas formed along the way** (it works better in high-volatility regimes; the breakout direction carries information beyond market drift) were each tested once on the holdout: volatility {'supported' if vol['supported'] else ('untestable' if not vol['testable'] else 'not supported')}, direction {'supported' if dirn['supported'] else 'not supported'}.
* {verdict_text}
{ctx_text}

**In plain English:** {plain} This is a statement about this specific rule on this data, not proof that no breakout strategy can work, and it is not investment advice.
"""

    partial_years = [str(int(r_['year'])) for _, r_ in ys.iterrows() if r_['partial']]
    partial_note = (f"Partial year(s) in the annual statistics: {', '.join(partial_years)} (the data cover only part of the year; the last holdout date is {pd.Timestamp(ho.dates.max()).date()}). " if partial_years else "")
    audit_lines = [l for l in resolve(cfg, cfg.paths.holdout_log).read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")] if resolve(cfg, cfg.paths.holdout_log).exists() else []
    banner = ("> **DRY RUN ON DEVELOPMENT DATA ONLY.** The \"holdout\" below is a pseudo-holdout (everything from " + str(res["split"].date()) + " on, inside the development period). "
              "Its numbers are NOT the final result. This run exists to test the pipeline before the real holdout is touched.\n\n") if dry else (
              "> Reproduction of the final run, for verification only. No decision may be taken from it.\n\n" if mode == "REPRODUCTION" else "")
    md = f"""# Final report: does a 15-minute opening range breakout on NQ have a real edge?

{banner}## 1. Bottom line

{conclusion}
## 2. How it was tested (and why the result can be trusted more than a typical backtest)

* **Data and timing.** Databento 1-minute NQ futures, 2010-2026, verified by hash; UTC converted to New York time with daylight saving checked; only the regular session; one raw-price contract per day chosen from the *previous* day's volume, so a contract roll can never create a fake gap or fake profit. Holidays, half-days, vendor-flagged and incomplete days are excluded and counted (Stage 2).
* **No lookahead.** Two independent implementations of the signals and of the fills (vectorised and bar-by-bar) must agree exactly; garbage written into the future must not move a trade; deliberately injected bugs must be caught by the tests (Stages 3-4, 7).
* **Costs and fills are explicit and conservative.** Commission ${cfg.execution.costs.commission_per_side_usd.NQ:.2f}/side per NQ (${cfg.execution.costs.commission_per_side_usd.MNQ:.2f} MNQ), {cfg.execution.costs.slippage_ticks:g} tick of slippage on market-type fills, a stop wins if stop and target are touched in the same minute, a gap through the stop loses the full gap. Results at 0x / 1x / 2x costs are shown.
* **No cherry-picking.** The 2024+ holdout stayed locked behind an explicit switch whose use is logged; all rules (grid, selection, tests, thresholds, and how the conclusion would be drawn) were written down and committed **before** the results they govern; {count_trials(cfg)} variants and hypotheses were logged for the multiple-testing correction; sensitivity used neighbourhood smoothing rather than the single best cell.
* **Honest uncertainty.** Every interval comes from a block bootstrap that respects clustering in time; all tests that were not pre-declared are described as hypotheses, not findings.

## 3. Headline statistics (primary specification, 1x costs)

{md_table(head)}

{partial_note}"Bootstrap share" is the fraction of 10,000 resamples whose mean was above zero.

![equity](fig18_final_equity.png)

## 4. The three tests declared before the holdout was opened

{md_table(tests, index=False)}

Three tests were declared, so a Bonferroni-adjusted level of 0.0167 is also relevant for the permutation test; the pre-declared rules in the table were applied unchanged.
Direction detail: mean hold-to-close return was {dirn['long_mean_bps']:+.2f} bps for longs and {dirn['short_mean_bps']:+.2f} bps for shorts (in trade direction), against an always-long benchmark of {dirn['always_long_mean_bps']:+.2f} bps.

## 5. Costs: the same trades at 0x, 1x and 2x

{md_table(cost_tbl, index=False)}

![costs](fig20_final_cost_scenarios.png)

## 6. By year

Development:

{md_table(yr_tbl(dev), index=False)}

Holdout (partial years marked *):

{md_table(yr_tbl(ho), index=False)}

![by year](fig19_final_by_year.png)

## 7. By regime and by side (development and holdout combined for regimes)

{md_table(reg_tbl, index=False)}

{md_table(side_tbl, index=False)}

"2023 onward" combines 2023 (development) with the holdout years. These cuts are descriptive; none was used to select anything.

## 8. Limitations and what could invalidate this

* **One history.** 2010-2026 is a single path through a long bull market in NQ; results can differ in other eras or instruments.
* **Assumed costs.** There is no quote data. Slippage and commission are assumptions (defaults used unless replaced by broker numbers in `config/config.yaml`); limit-order fills on touch are optimistic.
* **1-minute bars hide intrabar order.** The stop-first rule is conservative; in the data it affected very few trades (Stage 4).
* **A short holdout** ({s_ho['trades']:,} trades) can only detect large effects; "not distinguishable from zero" is not the same as "equal to zero".
* **Excluded days** (holidays, half-days, roll days, vendor-degraded or incomplete days) are a deliberate sample restriction.
* **Other designs.** Different ranges, filters, instruments or exits were only tested within the pre-declared grid.

## 9. Disclosure: one wording correction made after the result was known

The conclusion text is generated from templates written before the holdout was opened. The template for "the rules say no edge" assumed the holdout would not be positive, so its plain-English sentence ("no reason to expect to make money")
was wrong for what actually happened, and it was corrected after the result was seen (see `docs/DECISIONS.md`, D9). **The verdict ("No edge established"), the rules, and every number are unchanged**; only the explanatory wording
and the added context bullets in section 1 were changed.

## 10. Reproducing this and the audit trail

* One command: `python -m orb run` rebuilds every stage from the raw file (with `ORB_UNLOCK_HOLDOUT=yes` it also re-derives this final report, in reproduction mode). `python -m pytest` runs the tests.
* Trials registered for the multiple-testing correction: **{count_trials(cfg)}** (`logs/trials.jsonl`). Decision log: `docs/DECISIONS.md` (D1-D8). Frozen volatility thresholds: `logs/frozen_vol_thresholds.json`.
* Holdout access log (`logs/holdout_access.log`) has {len(audit_lines)} entr{'y' if len(audit_lines) == 1 else 'ies'}:

{chr(10).join('  * ' + l.split(chr(9))[0] + ' - ' + l.split(chr(9))[1] + ' - ' + l.split(chr(9))[3] for l in audit_lines)}
"""
    path = out_dir / "FINAL_REPORT.md"
    path.write_text(md, encoding="utf-8")
    return str(path)
