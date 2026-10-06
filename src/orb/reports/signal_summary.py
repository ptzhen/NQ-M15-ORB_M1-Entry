"""Stage 3 report: what the signal engine produces. Descriptive only, development data only.

No profit-and-loss appears here on purpose. Looking at outcomes before the execution model
(costs, slippage, stop/target rules) exists would invite tuning the signal to the results.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from orb.config import resolve
from orb.data.splits import load_bars_chosen, load_daily
from orb.reports.data_quality import BLUE, GRID, INK2, MUTED_BAR, ORANGE, SURFACE, _style, md_table
from orb.signals.orb import LONG, SHORT, generate_signals, params_from_cfg


def compute(cfg):
    p = params_from_cfg(cfg)
    daily = load_daily(cfg, "dev", purpose="stage 3 signal summary (descriptive, no P&L)")
    bars = load_bars_chosen(cfg, "dev", purpose="stage 3 signal summary (descriptive, no P&L)")
    dates = daily.index[daily["tradable"]]
    sig = generate_signals(bars, dates, p, tz=cfg.session.timezone)
    return p, daily, sig


def build_signal_report(cfg) -> str:
    out = resolve(cfg, cfg.paths.output_dir) / "signals"
    out.mkdir(parents=True, exist_ok=True)
    _style()
    p, daily, sig = compute(cfg)
    tick = cfg.data.tick_size
    pv = cfg.data.point_value_usd.NQ

    n_days = len(sig)
    t = sig[sig["direction"] != 0].copy()
    n_long, n_short = int((t["direction"] == LONG).sum()), int((t["direction"] == SHORT).sum())
    n_none = n_days - len(t)

    by_year = sig.groupby(sig.index.year)["direction"].agg(
        days="size", long=lambda s: int((s == LONG).sum()), short=lambda s: int((s == SHORT).sum()), no_signal=lambda s: int((s == 0).sum())
    )
    by_year["signal_rate"] = (by_year["long"] + by_year["short"]) / by_year["days"]

    sig_clock = t["signal_time"].dt.hour * 60 + t["signal_time"].dt.minute
    timing = pd.DataFrame(
        {"share of signals": [float((sig_clock < 10 * 60).mean()), float((sig_clock < 10 * 60 + 30).mean()), float((sig_clock >= 15 * 60).mean())]},
        index=["signal bar before 10:00 ET", "signal bar before 10:30 ET", "signal bar from 15:00 ET"],
    )

    width_pts = sig["range_width"]
    width_pct = (sig["range_width"] / sig["range_low"] * 100)
    q = [0.05, 0.25, 0.5, 0.75, 0.95]
    widths = pd.DataFrame({"points": width_pts.quantile(q).values, "ticks": (width_pts / tick).quantile(q).values,
                           "% of price": width_pct.quantile(q).values, "$ per NQ contract": (width_pts * pv).quantile(q).values},
                          index=[f"{int(x * 100)}th pct" for x in q])

    # adverse gap: how far the first price after the signal bar closes is from that close, in the breakout direction
    adverse = t["direction"] * (t["entry_open"] - t["signal_close"]) / tick
    # risk if the stop is the opposite side of the range, measured from the actual entry price
    risk = np.where(t["direction"] == LONG, t["entry_open"] - t["range_low"], t["range_high"] - t["entry_open"])
    risk = pd.Series(risk, index=t.index)
    degenerate = int((risk <= 0).sum())
    risk_ok = risk[risk > 0]
    risk_tbl = pd.DataFrame({"points": risk_ok.quantile(q).values, "$ per NQ contract": (risk_ok * pv).quantile(q).values,
                             "$ per MNQ contract": (risk_ok * cfg.data.point_value_usd.MNQ).quantile(q).values},
                            index=[f"{int(x * 100)}th pct" for x in q])
    gap_tbl = pd.DataFrame({"ticks": adverse.quantile([0.05, 0.25, 0.5, 0.75, 0.95, 0.99]).values},
                           index=["5th pct", "25th pct", "median", "75th pct", "95th pct", "99th pct"])

    timing.index.name = "when"
    widths.index.name = gap_tbl.index.name = risk_tbl.index.name = "percentile"

    # ---- figures ----
    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    bins = np.arange(9 * 60 + 45, 16 * 60, 5)
    ax.hist(sig_clock, bins=bins, color=BLUE, edgecolor=SURFACE, linewidth=1.5)
    ticks = list(range(10 * 60, 16 * 60, 60))
    ax.set_xticks(ticks, [f"{m // 60:02d}:00" for m in ticks])
    ax.set_xlabel("Signal bar time (ET), 5-minute bins")
    ax.set_ylabel("Number of signals (2010-2023)")
    ax.set_title("When breakouts are signalled")
    ax.grid(axis="x", visible=False)
    fig.tight_layout(); fig.savefig(out / "fig5_signal_times.png", dpi=140); plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    x = np.arange(len(by_year))
    ax.bar(x, by_year["long"], color=BLUE, width=0.7, edgecolor=SURFACE, linewidth=2, label="Long signal")
    ax.bar(x, by_year["short"], bottom=by_year["long"], color=ORANGE, width=0.7, edgecolor=SURFACE, linewidth=2, label="Short signal")
    ax.bar(x, by_year["no_signal"], bottom=by_year["long"] + by_year["short"], color=MUTED_BAR, width=0.7, edgecolor=SURFACE, linewidth=2, label="No signal")
    ax.set_xticks(x, [f"{y}*" if y == 2010 else str(y) for y in by_year.index], fontsize=9)
    ax.set_ylabel("Tradable days")
    ax.set_title("Signals per year, development period  (* partial year)")
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.28))
    fig.tight_layout(); fig.savefig(out / "fig6_signals_by_year.png", dpi=140); plt.close(fig)

    by_year_t = by_year.copy()
    by_year_t.index = by_year_t.index.astype(str)
    by_year_t.index.name = "year"
    for k in ("signal_rate",):
        by_year_t[k] = by_year_t[k].map(lambda v: f"{v:.1%}")

    first, last = sig.index.min().date(), sig.index.max().date()
    md = f"""# Signal engine report (Stage 3)

Development data only ({first} to {last}, tradable days). The 2024+ holdout was not loaded.
This report is descriptive: **no profit and loss is computed** (that needs the execution model of Stage 4).

## 1. Rules as implemented

| Rule | Value |
|---|---|
| Opening range | high/low of the first **{p.range_minutes}** one-minute bars (labelled 09:30 to 09:44 ET) |
| Range complete | 09:45:00 ET |
| Breakout | a bar **closes strictly beyond** the range (equal is not a breakout); judged on the close only |
| First possible signal bar | the bar labelled 09:45 (it closes at 09:46:00) |
| Entry | **open of the next bar** after the signal bar |
| Latest entry bar | {cfg.signal.last_entry_bar} ET (a later signal is not traded) |
| Trades per day | at most one (first breakout in either direction) |

## 2. How we know there is no lookahead

* **Reference implementation.** A bar-by-bar state machine that is fed one bar at a time (so it cannot see the future) must
  produce exactly the same signals as the vectorised engine: tested on 1,000 randomised days (range lengths 5/15/30/60) and on
  real development data.
* **Perturbation tests.** Overwriting every bar after the entry bar's open, the entry bar's own high/low/close, bars after the
  range window, or any other day, with random garbage leaves the signal unchanged; changing the signal bar's close or the entry bar's
  open *does* move it (so the test can fail).
* **Mutation tests.** Six deliberately injected bugs (range one bar too long, entry on the signal bar, breakout on the high,
  tie counted as breakout, entry at the next close, cut-off ignored) were each caught by the suite.
* **Timing convention.** A bar is labelled by its open, so its close is known one minute later. Signals use only closed bars.

Residual look-ahead in the *day universe* (not the signal): days with a missing minute later in the session are excluded, which a live
trader could not know at 09:46. That removes {int(daily['exclude_reason'].isin(['range_incomplete', 'window_incomplete']).sum())} development days (days removed for this reason alone; calendar
and roll exclusions are separate) and slightly favours quieter days.

## 3. Signal counts

| | Days | Share |
|---|---|---|
| Tradable development days | {n_days:,} | |
| Long signals | {n_long:,} | {n_long / n_days:.1%} |
| Short signals | {n_short:,} | {n_short / n_days:.1%} |
| No signal | {n_none:,} | {n_none / n_days:.1%} |

{md_table(by_year_t)}

2010 starts in June. The mix of long and short reflects only how often price leaves the range upward vs downward, not whether
either direction makes money.

![signals by year](fig6_signals_by_year.png)

## 4. When signals occur

{md_table(timing)}

![signal times](fig5_signal_times.png)

## 5. Opening-range width

{md_table(widths)}

## 6. Edge cases the execution model (Stage 4) must handle

* **Gap past the stop.** The entry bar can open beyond the opposite side of the range (so the risk would be zero or negative):
  **{degenerate}** of {len(t):,} signals ({degenerate / max(len(t), 1):.2%}). These cannot be traded with a range-based stop and need an explicit rule.
* **Entry slippage vs the signal close.** The first price after the signal bar closes, measured in the breakout direction
  (positive = worse for us than the close that triggered the signal):

{md_table(gap_tbl)}

* **Risk per trade** if the stop is the opposite side of the range, measured from the actual entry price (signals with positive risk):

{md_table(risk_tbl)}

  This is the denominator of every R-multiple. Commission and slippage are fixed per contract, so they weigh more on narrow-range days.

## 7. Parameters

All of these are in `config/config.yaml`: `session.range_minutes`, `session.cash_open`, `session.flat_time`, `signal.last_entry_bar`.
"""
    path = out / "signal_summary.md"
    path.write_text(md, encoding="utf-8")
    return str(path)
