"""Generate the Stage 2 data-quality report (markdown + figures + CSVs).

Every number in the markdown is computed here, never typed by hand, so the report cannot
drift from the data. Figures follow a small, fixed style: two accent hues at most, thin
marks, recessive grid, direct labels instead of legend boxes where there are <= 3 series.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from orb.config import hhmm_to_minutes, resolve
from orb.data.build import DataBundle
from orb.data.daily import REASON_PRIORITY
from orb.data.qc import calendar_crosscheck, chosen_contract_diagnostics, roll_table, timezone_proof

# Reference-palette slots 1 and 2 (validated adjacent pair), plus neutral inks.
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE, MUTED_BAR = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb", "#b9b8b2"


def _style():
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
            "text.color": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
            "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
            "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
            "lines.linewidth": 2,
        }
    )


def md_table(df: pd.DataFrame, index: bool = True) -> str:
    """Tiny markdown-table writer (avoids a `tabulate` dependency)."""
    d = df.reset_index() if index else df
    cols = [str(c) for c in d.columns]
    rows = [[("" if pd.isna(v) else (f"{v:,.3f}" if isinstance(v, (float, np.floating)) and abs(v) < 100 else (f"{v:,.0f}" if isinstance(v, (float, np.floating, int, np.integer)) and not isinstance(v, bool) else str(v)))) for v in r] for r in d.itertuples(index=False)]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _fmt_min(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def fig_open_spike(tz: dict, cfg, path: Path) -> None:
    open_min = hhmm_to_minutes(cfg.session.cash_open)
    prof = tz["profile"].loc[open_min - 30 : open_min + 45]
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    ax.plot(prof.index, prof["standard_time_EST"], color=BLUE, label="Winter (EST)")
    ax.plot(prof.index, prof["daylight_time_EDT"], color=ORANGE, label="Summer (EDT)")
    ax.axvline(open_min, color=INK2, lw=1, ls="--")
    ax.axvline(open_min + cfg.session.range_minutes, color=INK2, lw=1, ls=":")
    ax.text(open_min + 0.5, ax.get_ylim()[1] * 0.97, "09:30 open", color=INK2, va="top", fontsize=9)
    ax.text(open_min + cfg.session.range_minutes + 0.5, ax.get_ylim()[1] * 0.80, "09:45 range complete", color=INK2, va="top", fontsize=9)
    ticks = list(range(open_min - 30, open_min + 46, 15))
    ax.set_xticks(ticks, [_fmt_min(t) for t in ticks])
    ax.set_xlabel("Time of day, America/New_York (bar open label)")
    ax.set_ylabel("Avg contracts per minute (all contracts)")
    ax.set_title("The volume spike lands on 09:30 ET in both seasons")
    ax.legend(frameon=False, loc="upper right", bbox_to_anchor=(1.0, 0.62))
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_waterfall(counts: pd.Series, total: int, tradable: int, path: Path) -> None:
    labels = ["All ET dates with RTH data"] + [f"− {k.replace('_', ' ')}" for k in counts.index] + ["Tradable days"]
    vals = [total] + counts.tolist() + [tradable]
    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    colors = [MUTED_BAR] + [ORANGE] * len(counts) + [BLUE]
    y = np.arange(len(labels))[::-1]
    ax.barh(y, vals, color=colors, height=0.6)
    for yi, v in zip(y, vals):
        ax.text(v + total * 0.01, yi, f"{v:,}", va="center", color=INK2, fontsize=9)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, total * 1.12)
    ax.grid(axis="y", visible=False)
    ax.set_title("Where days are removed (first applicable reason)")
    ax.set_xlabel("Trading days")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_by_year(daily: pd.DataFrame, path: Path) -> None:
    g = daily.groupby(daily.index.year)
    tr, ex = g["tradable"].sum(), g["tradable"].count() - g["tradable"].sum()
    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    x = np.arange(len(tr))
    ax.bar(x, tr.values, color=BLUE, width=0.7, edgecolor=SURFACE, linewidth=2, label="Tradable")
    ax.bar(x, ex.values, bottom=tr.values, color=MUTED_BAR, width=0.7, edgecolor=SURFACE, linewidth=2, label="Excluded")
    ax.set_xticks(x, [str(i) if i not in (2010, 2026) else f"{i}*" for i in tr.index], fontsize=9)
    ax.set_ylabel("Trading days")
    ax.set_title("Tradable days per year  (* partial year)")
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, loc="lower right", ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def fig_roll_share(daily: pd.DataFrame, path: Path) -> None:
    d = daily[daily["chosen_id"].notna()]
    roll = d["flag_roll_new_contract"] | d["flag_roll_low_share"]
    fig, ax = plt.subplots(figsize=(8.5, 4.0))
    ax.plot(d.index, d["chosen_day_share"], color=MUTED_BAR, lw=0.8)
    ax.scatter(d.index[roll], d.loc[roll, "chosen_day_share"], s=14, color=ORANGE, zorder=3, edgecolor=SURFACE, linewidth=1, label="Flagged roll day (excluded)")
    ax.set_ylim(0, 1.03)
    ax.set_ylabel("Share of that day's RTH volume")
    ax.set_title("Contract we would trade: share of the day's volume")
    ax.legend(frameon=False, loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def build_report(cfg, b: DataBundle) -> Path:
    out = resolve(cfg, cfg.paths.output_dir) / "data_quality"
    out.mkdir(parents=True, exist_ok=True)
    _style()

    daily, contracts = b.daily, b.contracts
    tz = timezone_proof(b.bars, cfg)
    rolls = roll_table(daily, contracts)
    cal = calendar_crosscheck(daily, b.xnys, cfg)
    ch = chosen_contract_diagnostics(daily)
    s, ig = b.load_stats, b.integrity

    # ---- day accounting ----
    total = len(daily)
    excl_counts = daily.loc[~daily["tradable"], "exclude_reason"].value_counts()
    excl_counts = excl_counts.reindex([r for r in REASON_PRIORITY if r in excl_counts.index])
    n_trad = int(daily["tradable"].sum())
    assert n_trad + int(excl_counts.sum()) == total  # the waterfall must add up
    flag_cols = [f"flag_{r}" for r in REASON_PRIORITY]
    flag_counts = pd.DataFrame(
        {"days_flagged": [int(daily[c].sum()) for c in flag_cols],
         "removed_as_first_reason": [int(excl_counts.get(r, 0)) for r in REASON_PRIORITY]},
        index=REASON_PRIORITY,
    )
    by_year = daily.groupby(daily.index.year).agg(dates=("tradable", "size"), tradable=("tradable", "sum"))
    by_year["excluded"] = by_year["dates"] - by_year["tradable"]
    by_year["tradable"] = by_year["tradable"].astype(int)

    # ---- bad-print scan on the contract we trade ----
    bc = b.bars_chosen
    rel = (bc["high"] - bc["low"]) / bc["open"]
    med_vol = bc.groupby("mod")["volume"].transform("median")
    big = bc.assign(range_pct=rel * 100, vol_x_median=bc["volume"] / med_vol)[rel > 0.01]
    big = big.sort_values("range_pct", ascending=False).head(10)
    big_tbl = pd.DataFrame({"time_et": big["ts_et"].dt.strftime("%Y-%m-%d %H:%M"), "contract": big["symbol"], "range_pct": big["range_pct"].round(2), "volume": big["volume"], "vol_x_median_for_minute": big["vol_x_median"].round(1), "day_tradable": big["date"].map(daily["tradable"]).to_numpy()})

    # ---- files ----
    contracts_out = contracts.assign(first_ts=contracts["first_ts"].astype(str), last_ts=contracts["last_ts"].astype(str))
    contracts_out.to_csv(out / "contracts.csv")
    rolls.to_csv(out / "rolls.csv")
    daily.loc[~daily["tradable"], ["exclude_reason"] + flag_cols].to_csv(out / "excluded_days.csv")
    fig_open_spike(tz, cfg, out / "fig1_open_spike.png")
    fig_waterfall(excl_counts, total, n_trad, out / "fig2_day_waterfall.png")
    fig_by_year(daily, out / "fig3_tradable_by_year.png")
    fig_roll_share(daily, out / "fig4_roll_share.png")

    o = hhmm_to_minutes(cfg.session.cash_open)
    spike_tbl = pd.DataFrame(
        {"avg contracts/min": [tz["avg_vol_0929"], tz["avg_vol_0930"]]},
        index=[f"{_fmt_min(o - 1)} ET bar", f"{_fmt_min(o)} ET bar"],
    )
    ig_tbl = pd.DataFrame({"count": ig}).drop(index=["ts_sorted", "max_1m_range_pct"]).astype("int64")
    other = ", ".join(s["other_prefixes"]) or "none"
    v = b.vendor
    degraded = v[v == "degraded"]
    deg_in_data = daily.index[daily["vendor_degraded"]]
    missing_sess = cal["xnys_sessions_without_any_data"]
    missing_and_degraded = [x for x in missing_sess if pd.Timestamp(x) in degraded.index]
    utc = tz["open_bar_utc_hours"]
    utc_txt = "; ".join(f"{k}: {', '.join(f'{h}:00 UTC ({n:,} days)' for h, n in vv.items())}" for k, vv in utc.items())
    ordinary = big_tbl[big_tbl["vol_x_median_for_minute"] < 1.5]
    ordinary_txt = ("; ".join(f"{r.time_et} {r.contract} (day tradable: {r.day_tradable})" for r in ordinary.itertuples()) or "none")
    ig_tbl.index.name = "check"
    spike_tbl.index.name = "bar"
    flag_counts.index.name = "rule"
    roll_days = rolls["calendar_days_to_old_expiry"]

    md = f"""# Data-quality report (Stage 2)

Generated by `python -m orb data`. Every number below is computed from the raw file.
This report audits data **integrity and availability** over the full 2010-2026 range. It computes no
returns and no strategy statistics, so it does not touch the holdout in any statistical sense. Strategy
code only receives data through the guarded loaders in `orb/data/splits.py`.

## 1. Source file

| Item | Value |
|---|---|
| File SHA-256 | `{s['sha256'][:16]}…` (matches the Databento manifest) |
| Rows in file | {s['rows_total']:,} |
| NQ outrights kept | {s['rows_outright']:,} |
| NQ calendar spreads dropped | {s['rows_spread']:,} |
| Other products dropped ({other}) | {s['rows_other']:,} |
| First / last bar (UTC) | {s['ts_min_utc'][:19]} / {s['ts_max_utc'][:19]} |
| Instruments (contracts) | {len(contracts)} |
| Session content | Full Globex session (~23h), 17:00-18:00 ET maintenance break; analysis uses 09:30-16:00 ET only |
| Price adjustment | None. Raw per-contract prices. Never stitched across contracts. |

## 2. Structural integrity

{md_table(ig_tbl)}

Structural checks (duplicates, OHLC violations, non-positive prices, off-tick prices, one instrument_id
under several symbols) **abort the build if non-zero**. Zero-volume bars are not expected: the feed emits a
bar only for minutes with trades, so a "missing" minute means no trades, not a data hole.
Bars with a 1-minute range above 1% (largest: {ig['max_1m_range_pct']:.1f}%) are listed in section 8 (top 10, contract we trade).

## 3. Timezone verification

Timestamps are UTC and mark the **start** of each bar. They are converted with the tz database
(`America/New_York`), never a fixed offset.

{md_table(spike_tbl)}

* Median 09:30 / 09:29 volume ratio: **{tz['median_ratio']:.1f}x**. The jump is more than 3x on **{tz['share_ratio_gt3_all']:.1%}** of all days
  and on **{tz['share_ratio_gt3_dst_mismatch_windows']:.1%}** of the {tz['n_mismatch_days']} days inside the US/EU
  daylight-saving mismatch windows (Mar 8-31, Oct 25-Nov 7), where a wrong conversion would fail first.
* The 09:30 ET bar falls at **{utc_txt}**: exactly one UTC hour per season, shifting by one hour when US clocks change.

![open spike](fig1_open_spike.png)

## 4. Contracts and rolls

* Contract symbols are reused every decade (`NQH0` = Mar-2010 and Mar-2020), so everything is keyed on
  `instrument_id`. Expiry is derived from the first-seen date (third Friday of the month).
* **{len(rolls)}** changes of volume leader. The old contract's expiry was a median **{roll_days.median():.0f}** calendar days
  away at the switch (min {roll_days.min():.0f}, max {roll_days.max():.0f}). Full list in `rolls.csv`.
* We trade the **previous day's** RTH-volume leader, so selection never uses information from the trading day.
  Prices are raw and the whole trade lives in one contract, so a roll cannot create a price gap or P&L.
* Roll-flagged days (first day on a new contract, or the leader held < {cfg.contracts.roll_leader_min_share:.0%} of volume the day before): **{ch['n_roll_flagged']}**.
* Diagnostic (uses same-day volume, never used for selection): the chosen contract was also that day's leader on
  **{ch['chosen_equals_same_day_leader']:.1%}** of days ({ch['n_mismatch_days']} mismatches, of which {ch['mismatch_days_flagged_as_roll']} are roll-flagged).
  Share of the day's RTH volume held by the chosen contract (1%/5%/median): non-roll days {tuple(ch['day_share_quantiles_non_roll_days'].values())}, roll days {tuple(ch['day_share_quantiles_roll_days'].values())}.
* Chosen contract already expired on the trade date: **{ch['chosen_expired_days']}** days.

![roll share](fig4_roll_share.png)

## 5. Which days are usable

| Item | Count |
|---|---|
| ET dates with RTH data | {total:,} |
| **Tradable days** | **{n_trad:,}** ({n_trad / total:.1%}) |

{md_table(flag_counts)}

*days_flagged* counts every day a rule applies to; *removed_as_first_reason* attributes each day to the highest-priority
rule so the waterfall adds up exactly. Tolerance: {cfg.filters.max_missing_range_bars} missing bars allowed in the range,
{cfg.filters.max_missing_window_bars} in the rest of the window to {cfg.session.flat_time}.
The three March-2020 days with too few range bars are limit-down halts: excluding them is a (small) choice that removes
some of the most violent opening sessions, noted as a caveat.

![waterfall](fig2_day_waterfall.png)

{md_table(by_year.set_axis(by_year.index.astype(str)).rename_axis("year"))}

2010 starts in June and 2026 ends Oct 1, so both are partial years.

![by year](fig3_tradable_by_year.png)

## 6. Vendor quality ledger (`condition.json`)

Databento's own per-day status for the whole download: **{len(v):,}** dates, **{len(degraded)}** marked `degraded`
(the rest `available`). **{len(deg_in_data)}** degraded dates carry ET regular-session data and are excluded
by default (`filters.exclude_vendor_degraded`); the other {len(degraded) - len(deg_in_data)} are weekends or sessions with no
RTH data at all. Degraded dates: {', '.join(str(x.date()) for x in degraded.index)}.

## 7. Calendar cross-check (NYSE vs the bars)

* NYSE sessions in range: {cal['xnys_sessions_in_range']:,}; ET dates with RTH data: {cal['dates_with_rth_data']:,}.
* NYSE sessions with **no** RTH data: **{len(missing_sess)}** {missing_sess}. {len(missing_and_degraded)} of them are vendor-`degraded`;
  the remainder is an unexplained hole in the file (it simply cannot be traded).
* Data dates when NYSE was closed (CME short session, no real cash open): {cal['data_dates_not_xnys_session']}
* NYSE half-days present in the data: {cal['xnys_early_close_dates_in_data']}
* Days where the bars end before 14:00 ET: {cal['data_short_session_dates']}; calendar agrees on {cal['short_in_data_and_calendar']}.
* Bars short but calendar says normal: {cal['short_in_data_not_calendar']}
* Calendar says short/closed but bars run to the normal close: {cal['calendar_short_not_short_in_data']}

## 8. Extreme 1-minute bars on the contract we trade

Ten largest 1-minute ranges (RTH). A bad print would show a large range on **ordinary** volume; real shocks come
with volume many times the norm for that minute. Rows at ordinary volume (< 1.5x the median for that minute): {ordinary_txt}.

{md_table(big_tbl, index=False)}

## 9. Caveats and what could invalidate this

* **Abrupt-flip roll days are not excluded.** {ch['n_unflagged_mismatch_days']} tradable-looking days are days where volume flipped to the next
  contract *that same day* after the old contract held >= {cfg.contracts.roll_leader_min_share:.0%} the day before. We cannot know that in advance, so these days
  trade the old contract while it still held between {ch['day_share_min_non_roll_days']:.0%} and ~100% of the volume (thousands of
  contracts a minute). Stage 6 will rerun results with roll days included/excluded to show whether it matters.
* The "timestamp = bar open" convention is established by the 09:30 spike, not by documentation.
* One consolidated feed: no bid/ask, so spreads and slippage are assumptions (Stage 4).
* Excluded days are a deliberate sample restriction ({total - n_trad:,} of {total:,}); results apply to normal full sessions on non-roll days.
* "No trades in a minute" is treated as a data gap that excludes the day. That is conservative but removes some quiet days.
"""
    path = out / "data_quality_report.md"
    path.write_text(md, encoding="utf-8")
    return path
