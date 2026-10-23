"""Chart of price with VWAP and bands in the style of TradingView's built-in indicator.

Colours follow the Pine script (VWAP #2962FF, band 1 green, band 2 olive, band 3 teal, each filled at ~95% transparency)
because the point is to look like the TradingView indicator. Development-partition data only: the holdout is spent, and
even viewing it would need the logged unlock, so this command refuses dates on or after ``splits.holdout_start``.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from orb.config import resolve
from orb.data.splits import load_bars_chosen
from orb.indicators.vwap import vwap
from orb.reports.data_quality import INK, INK2, SURFACE, _style

VWAP_BLUE = "#2962FF"
BAND_COLORS = {1: "#4CAF50", 2: "#808000", 3: "#089981"}     # Pine: color.green, color.olive, color.teal


def build_vwap_chart(cfg, date: str | None = None, days: int = 1, anchor: str | None = None, band_mode: str | None = None,
                     bands: dict | None = None, source: str | None = None, offset: int | None = None, out: str | None = None) -> tuple[str, pd.DataFrame]:
    d = cfg.indicators.vwap
    anchor, source, band_mode = anchor or d.anchor, source or d.source, band_mode or d.band_mode
    bands = dict(d.bands) if bands is None else bands
    offset = d.offset if offset is None else offset

    bars = load_bars_chosen(cfg, "dev", purpose="VWAP chart (development data only)").sort_values("ts_event").reset_index(drop=True)
    dates = sorted(bars["date"].unique())
    last = pd.Timestamp(date) if date else dates[-1]
    if last >= pd.Timestamp(cfg.splits.holdout_start):
        raise SystemExit(f"{last.date()} is in the holdout (>= {cfg.splits.holdout_start}), which is spent and locked. Choose an earlier date.")
    window_dates = [x for x in dates if x <= last][-days:]
    if not window_dates:
        raise SystemExit(f"no trading dates on or before {last.date()}")

    # compute on ALL development bars so Week/Month/... anchors accumulate from their true start, then slice the window
    res = vwap(bars, anchor=anchor, source=source, band_mode=band_mode, bands=bands, offset=offset, tz=cfg.session.timezone,
               session_reset=d.session_reset)
    sel = bars["date"].isin(window_dates).to_numpy()
    b, r = bars[sel].reset_index(drop=True), res[sel].reset_index(drop=True)

    _style()
    fig, ax = plt.subplots(figsize=(11, 5.2))
    x = np.arange(len(b))
    ax.plot(x, b["close"], color=INK2, lw=0.9, label="Close (1-min)", zorder=2)
    for pid, idx in r.groupby("period").groups.items():          # a separate segment per anchor period: no line across a reset
        i = np.asarray(list(idx))
        ax.plot(x[i], r.loc[i, "vwap"], color=VWAP_BLUE, lw=1.8, label="VWAP" if pid == r["period"].iloc[0] else None, zorder=4)
        for k in sorted(bands):
            c = BAND_COLORS.get(k, "#808080")
            ax.plot(x[i], r.loc[i, f"upper_{k}"], color=c, lw=1.0, zorder=3)
            ax.plot(x[i], r.loc[i, f"lower_{k}"], color=c, lw=1.0, zorder=3)
            ax.fill_between(x[i], r.loc[i, f"lower_{k}"], r.loc[i, f"upper_{k}"], color=c, alpha=0.06, lw=0, zorder=1,
                            label=(f"Band #{k} (+/-{bands[k]:g}{'%' if band_mode == 'Percentage' else ' st.dev.'})" if pid == r["period"].iloc[0] else None))
    starts = b.groupby("date").head(1).index.to_numpy()
    ax.set_xticks(starts, [f"{t:%b %d}" for t in b.loc[starts, "date"]] if days > 1 else [f"{b['date'].iloc[0]:%Y-%m-%d} 09:30"], fontsize=9)
    if days == 1:
        ticks = list(range(0, len(b), 60))
        ax.set_xticks(ticks, [f"{b['ts_et'].iloc[i]:%H:%M}" for i in ticks], fontsize=9)
    ax.set_ylabel("NQ price (raw, chosen contract)")
    sub = f"{band_mode}, source {source}" + (f", offset {offset}" if offset else "")
    ax.set_title(f"NQ VWAP ({anchor} anchor): {window_dates[0]:%Y-%m-%d}" + (f" to {window_dates[-1]:%Y-%m-%d}" if days > 1 else "") + f"   [{sub}]", fontsize=10)
    ax.legend(frameon=False, loc="best", fontsize=8)
    ax.margins(x=0.01)
    fig.tight_layout()
    path = resolve(cfg, out) if out else resolve(cfg, cfg.paths.output_dir) / "vwap" / f"vwap_{anchor.lower()}_{window_dates[-1]:%Y%m%d}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140)
    plt.close(fig)
    table = pd.concat([b[["ts_et", "close", "volume"]], r.drop(columns="period")], axis=1)
    return str(path), table
