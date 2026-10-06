"""Stage 4 report: the execution model and what costs do to the primary specification.

Development data only. The strategy parameters are the ones fixed in the brief (15-minute range,
stop at the opposite side, 1R target); nothing here is tuned. Numbers are shown to expose the
sensitivity of the result to cost and fill assumptions, not to pick a favourite.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from orb.analysis.metrics import summarize
from orb.analysis.trials import count_trials, record_trial
from orb.config import resolve
from orb.data.splits import load_bars_chosen, load_daily
from orb.execution.simulate import exec_params_from_cfg, simulate
from orb.reports.data_quality import BLUE, GRID, INK2, ORANGE, SURFACE, _style, md_table
from orb.signals.orb import generate_signals, params_from_cfg

AQUA = "#1baf7a"      # reference-palette slot 3 (slots 1-3 validate for all pairs)


def _run(bars, sig, orb, cfg, **ov):
    return simulate(bars, sig, orb, exec_params_from_cfg(cfg, **ov))


def _row(trades: pd.DataFrame) -> dict:
    s = summarize(trades)
    r = trades["r_multiple"]
    se = r.std(ddof=1) / np.sqrt(len(r))
    return {
        "trades": f"{s['trades']:,}",
        "win rate": f"{s['win_rate']:.1%}",
        "expectancy (R)": f"{s['expectancy_r']:+.3f}",
        "95% CI (R)": f"{s['expectancy_r'] - 1.96 * se:+.3f} to {s['expectancy_r'] + 1.96 * se:+.3f}",
        "profit factor": f"{s['profit_factor']:.2f}",
        "avg net pts": f"{s['avg_net_points']:+.2f}",
        "total $ (1 NQ)": f"{s['total_usd_nq']:+,.0f}",
        "total $ (1 MNQ)": f"{s['total_usd_mnq']:+,.0f}",
    }


def build_execution_report(cfg) -> str:
    out = resolve(cfg, cfg.paths.output_dir) / "execution"
    out.mkdir(parents=True, exist_ok=True)
    _style()

    orb = params_from_cfg(cfg)
    purpose = "stage 4 execution report (development data, primary spec, no tuning)"
    daily = load_daily(cfg, "dev", purpose=purpose)
    bars = load_bars_chosen(cfg, "dev", purpose=purpose)
    dates = daily.index[daily["tradable"]]
    sig = generate_signals(bars, dates, orb, tz=cfg.session.timezone)
    ex = exec_params_from_cfg(cfg)
    tick, pv = cfg.data.tick_size, cfg.data.point_value_usd

    base, skipped = _run(bars, sig, orb, cfg)                       # 1x costs, primary spec
    n_sig = int((sig["direction"] != 0).sum())

    # ---- cost scale: 0x / 1x / 2x ----
    scales = {0.0: "0x (frictionless)", 1.0: "1x (base)", 2.0: "2x (double)"}
    by_scale = {k: _run(bars, sig, orb, cfg, cost_scale=k)[0] for k in scales}
    scale_tbl = pd.DataFrame({v: _row(by_scale[k]) for k, v in scales.items()}).T
    scale_tbl.index.name = "cost level"

    # ---- slippage grid at 1x commission ----
    slip_tbl = pd.DataFrame({f"{s:g} tick{'s' if s != 1 else ''}": _row(_run(bars, sig, orb, cfg, slippage_ticks=s)[0]) for s in (0, 1, 2, 3)}).T
    slip_tbl.index.name = "slippage per market fill"

    # ---- fill assumption sensitivity ----
    variants = {
        "primary: stop wins ties, target fills on touch": {},
        "target must trade 1 tick THROUGH": {"target_fill": "through"},
        "optimistic bound: ties go to the target": {"ambiguity": "target_first"},
    }
    var_tbl = pd.DataFrame({k: _row(_run(bars, sig, orb, cfg, **v)[0]) for k, v in variants.items()}).T[["trades", "win rate", "expectancy (R)", "profit factor", "total $ (1 NQ)"]]
    var_tbl.index.name = "fill assumption (1x costs)"

    # ---- mechanics ----
    reasons = base["exit_reason"].value_counts()
    reason_tbl = pd.DataFrame({"trades": reasons, "share": (reasons / len(base)).map("{:.1%}".format)})
    reason_tbl["avg R"] = base.groupby("exit_reason")["r_multiple"].mean().reindex(reasons.index).map("{:+.2f}".format)
    reason_tbl.index.name = "exit reason"
    n_amb = int(base["ambiguous_bar"].sum())
    gaps = base[base["exit_reason"] == "stop_gap"]
    gap_extra_r = (gaps["r_multiple"] + 1.0) if len(gaps) else pd.Series(dtype=float)

    # ---- cost drag by risk size ----
    cost_usd = base["slippage_points"] * pv.NQ + base["commission_usd_nq"]
    risk_usd = base["risk_points"] * pv.NQ
    q = pd.qcut(base["risk_points"], 4, labels=["narrowest quarter", "2nd", "3rd", "widest quarter"])
    med = base.groupby(q, observed=True)["risk_points"].median()
    drag = pd.DataFrame({
        "median risk (pts)": med.map("{:.1f}".format),
        "median risk ($ NQ)": (med * pv.NQ).map("${:,.0f}".format),
        "avg cost ($ NQ)": cost_usd.groupby(q, observed=True).mean().map("${:.1f}".format),
        "cost as % of risk": (cost_usd / risk_usd).groupby(q, observed=True).mean().map("{:.1%}".format),
    })
    drag.index.name = "range width"

    # ---- chart: cumulative net R at 0x / 1x / 2x ----
    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    for (k, label), color in zip(scales.items(), (BLUE, ORANGE, AQUA)):
        cum = by_scale[k]["r_multiple"].cumsum()
        ax.plot(cum.index, cum.values, color=color, lw=2)
        ax.text(cum.index[-1], cum.values[-1], f"  {label.split(' ')[0]}", color=INK2, va="center", fontsize=9)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_ylabel("Cumulative net R (1 contract, trade by trade)")
    ax.set_title("Primary spec, development data: cumulative R at 0x / 1x / 2x costs")
    ax.margins(x=0.06)
    fig.tight_layout(); fig.savefig(out / "fig7_cost_levels.png", dpi=140); plt.close(fig)

    trial_params = {"range_minutes": orb.range_minutes, "stop_range_fraction": ex.stop_fraction, "target_r": ex.target_r,
                    "target_fill": ex.target_fill, "ambiguity": ex.ambiguity, "last_entry_bar": cfg.signal.last_entry_bar}
    is_new = record_trial(cfg, "stage4", "primary_spec", trial_params, {"expectancy_r_1x": float(base["r_multiple"].mean()), "trades": len(base)})
    base.to_csv(out / "trades_primary_dev.csv")

    cs = cfg.execution.costs
    md = f"""# Execution-model report (Stage 4)

Development data only ({dates.min().date()} to {dates.max().date()}). The 2024+ holdout was not loaded.
Strategy parameters are the ones fixed in the brief and are **not tuned**: {orb.range_minutes}-minute range, stop at the opposite side of the range,
{ex.target_r:g}R target, one contract, one trade per day. This report is about how fragile the result is to cost and fill assumptions.
It is **not** the full backtest (Stage 5 adds the trade log and by-year views; Stage 8 the statistics and confidence intervals).

## 1. Execution rules as implemented

| Step | Rule |
|---|---|
| Entry | market order at the open of the bar after the signal, filled **{cs.slippage_ticks:g} tick(s)** ({cs.slippage_ticks * tick:g} pt) worse at 1x |
| Levels | set from the **actual fill**: R = fill to stop; target = fill +/- {ex.target_r:g} x R, rounded to the tick *away* from price (harder to hit) |
| Stop | opposite side of the opening range. Touched when the bar's low/high reaches it. **Filled {cs.slippage_ticks:g} tick worse; if a later bar opens beyond it, we get that open (full gap loss)** |
| Target | resting limit order: fills at the target price on touch, no slippage ({cs.limit_slippage_ticks:g} ticks). A gap past the target is not credited |
| Same-bar stop and target | **stop assumed first** (conservative). Flagged on each trade; optimistic bound shown in section 5 |
| Entry bar | checked too: it opens at our price, so its whole range comes after entry |
| Time exit | market order at the open of the {cfg.session.flat_time} bar, {cs.slippage_ticks:g} tick worse |
| Skipped trades | risk below {cfg.execution.min_risk_ticks:g} tick (e.g. entry gaps past the stop): counted, never silently dropped |
| Commission | ${cs.commission_per_side_usd.NQ:.2f} per side per NQ, ${cs.commission_per_side_usd.MNQ:.2f} per side per MNQ (round trip = 2 sides) |
| Spread | not modelled separately (no quote data). It is folded into the slippage ticks, which are all-in adverse cost per market fill |
| Cost scale | multiplies commission **and** slippage together: 0x frictionless, 1x base, 2x double |

NQ tick = {tick} pt = ${tick * pv.NQ:.2f}; MNQ tick = ${tick * pv.MNQ:.2f}. Note MNQ commission is a much larger share of its P&L than NQ commission.

## 2. Trade mechanics at 1x costs

* Signals: {n_sig:,}. Trades taken: **{len(base):,}**. Skipped for tiny/negative risk: **{len(skipped)}**.
* Same-bar stop-and-target ambiguity: **{n_amb}** trades ({n_amb / len(base):.1%}), all booked as stop losses.
* Gap-through-stop exits: **{len(gaps)}**{f", average extra loss {gap_extra_r.mean():.2f}R beyond the 1R stop" if len(gaps) else ""}.

{md_table(reason_tbl)}

## 3. Results at 0x / 1x / 2x costs

{md_table(scale_tbl)}

*95% CI* is the naive interval for mean R treating trades as independent (it ignores clustering by regime, so it is too narrow);
Stage 7 replaces it with a bootstrap. Profit factor = gross wins / gross losses in dollars (NQ).

![cost levels](fig7_cost_levels.png)

## 4. Slippage grid (commission at 1x)

{md_table(slip_tbl)}

## 5. How much do the fill assumptions matter?

{md_table(var_tbl)}

The stop-first rule and the touch-fill rule bound what 1-minute bars can tell us. The distance between the pessimistic and optimistic rows is the
irreducible uncertainty from not having tick data.

## 6. Cost drag depends on range width (1x costs)

{md_table(drag)}

Costs are roughly fixed in dollars while risk scales with the range, so narrow-range days pay a far larger fraction of their risk in costs.

## 7. What could invalidate this

* **Slippage is an assumption.** 1 tick all-in per market fill is plausible for NQ in liquid RTH, but breakout entries and stops fill into moving
  markets and can be worse, particularly at the open and on news. The 2-3 tick rows are not paranoid.
* **Limit-order fills on touch are optimistic**: in reality a touch does not guarantee a fill. The "through" row is a proxy, not the truth.
* **No latency model.** We assume the entry bar's open is obtainable right after the signal bar closes.
* **1-minute bars hide intrabar order.** Section 2 counts how often that matters.
* Trials recorded so far (for the multiple-testing correction): **{count_trials(cfg)}** ({'new entry added' if is_new else 'already registered'}).
"""
    path = out / "execution_report.md"
    path.write_text(md, encoding="utf-8")
    return str(path)
