"""Per-day table: which contract we would trade, and whether the day is usable.

Two design decisions live here, both aimed at preventing fake signals / fake P&L:

1. **One contract per day, chosen from yesterday's information.**
   We trade the contract with the highest RTH volume on the *previous* trading day.
   Using today's volume leader would be lookahead. Because the whole trade happens
   inside one RTH session in one contract, a roll can never create a price gap or
   fake P&L: there is no stitching of two contracts, and prices are never adjusted.

2. **Every day gets flags; exclusion is a separate, configurable step.**
   The flags are always computed so the data-quality report can say exactly how many
   days each rule removes. ``exclude_reason`` records the *first* applicable reason
   (priority order below) so the day-count waterfall adds up.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from orb.config import hhmm_to_minutes

# Order matters: it decides which reason is reported when several apply.
REASON_PRIORITY = [
    "no_prev_day",
    "no_chosen_bars",
    "cash_closed",
    "early_close",
    "vendor_degraded",
    "range_incomplete",
    "window_incomplete",
    "roll_new_contract",
    "roll_low_share",
]


def add_et_columns(bars: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Add ET wall-clock columns. ``tz_convert`` is DST-aware (it uses the tz database,
    never a fixed UTC offset). ``date`` is the naive ET calendar date; ``mod`` is the
    minute-of-day in ET."""
    ts_et = bars["ts_event"].dt.tz_convert(tz)
    wall = ts_et.dt.tz_localize(None)
    return bars.assign(
        ts_et=ts_et,
        date=wall.dt.normalize(),
        mod=(wall.dt.hour * 60 + wall.dt.minute).astype("int16"),
    )


def build_daily(bars: pd.DataFrame, contracts: pd.DataFrame, xnys: pd.DataFrame, vendor: pd.Series, cfg) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (daily table, RTH bars of the chosen contract).

    ``bars`` must already carry ``date`` / ``mod`` (see ``add_et_columns``).
    """
    open_min = hhmm_to_minutes(cfg.session.cash_open)
    close_min = hhmm_to_minutes(cfg.session.cash_close)
    flat_min = hhmm_to_minutes(cfg.session.flat_time)
    range_len = int(cfg.session.range_minutes)

    rth = bars[(bars["mod"] >= open_min) & (bars["mod"] < close_min)]

    # --- volume leader per day (deterministic tie-break on instrument_id) ---
    vol = rth.groupby(["date", "instrument_id"], sort=True)["volume"].sum().reset_index()
    total = vol.groupby("date")["volume"].sum()
    ordered = vol.sort_values(["date", "volume", "instrument_id"], ascending=[True, False, True])
    lead = ordered.groupby("date").head(1).set_index("date")

    daily = pd.DataFrame(index=total.index)
    daily["rth_volume"] = total
    daily["leader_id"] = lead["instrument_id"]
    daily["leader_share"] = lead["volume"] / total
    daily["last_bar_min_any"] = rth.groupby("date")["mod"].max()

    # --- choose tomorrow's contract from today's leader: strictly past information ---
    daily["chosen_id"] = daily["leader_id"].shift(1).astype("Int64")
    daily["chosen_prev_share"] = daily["leader_share"].shift(1)

    chosen_filled = daily["chosen_id"].fillna(-1).astype("int64")
    # Diagnostic only (uses same-day volume, never used to select or exclude days).
    vol_idx = vol.set_index(["date", "instrument_id"])["volume"]
    pairs = pd.MultiIndex.from_arrays([daily.index, chosen_filled])
    chosen_day_vol = vol_idx.reindex(pairs).to_numpy()
    daily["chosen_day_share"] = chosen_day_vol / daily["rth_volume"].to_numpy()
    daily["chosen_is_day_leader"] = (chosen_filled == daily["leader_id"].astype("int64")).where(daily["chosen_id"].notna())

    daily["chosen_symbol"] = daily["chosen_id"].map(contracts["symbol"])
    daily["chosen_expiry"] = daily["chosen_id"].map(contracts["expiry"])

    # --- bar completeness of the chosen contract from open through flat_time ---
    # Databento emits a bar only when trades occurred, so "missing" = no trades that minute.
    n_exp = flat_min - open_min + 1
    cid = daily[["chosen_id"]].dropna().rename(columns={"chosen_id": "_cid"})  # first date has no prior day
    sel = rth[rth["mod"] <= flat_min].merge(cid, left_on="date", right_index=True)
    sel = sel[sel["instrument_id"] == sel["_cid"].astype("int64")]
    present = np.zeros((len(daily), n_exp), dtype=bool)
    present[daily.index.get_indexer(sel["date"]), (sel["mod"] - open_min).to_numpy()] = True
    daily["n_missing_range"] = (~present[:, :range_len]).sum(axis=1)
    daily["n_missing_window"] = (~present[:, range_len:]).sum(axis=1)

    # --- calendar facts ---
    daily["xnys_session"] = daily.index.isin(xnys.index)
    daily["xnys_early_close"] = xnys["early_close"].reindex(daily.index).fillna(False).astype(bool)

    daily["vendor_degraded"] = vendor.reindex(daily.index).eq("degraded").to_numpy()

    daily = classify_days(daily, cfg, n_exp)

    # --- RTH bars of the chosen contract (all days; flags decide usability) ---
    cb = rth.merge(cid, left_on="date", right_index=True)
    bars_chosen = cb[cb["instrument_id"] == cb["_cid"].astype("int64")].drop(columns="_cid")
    bars_chosen = bars_chosen[["date", "ts_event", "ts_et", "mod", "instrument_id", "symbol", "open", "high", "low", "close", "volume"]]
    bars_chosen = bars_chosen.sort_values("ts_event").reset_index(drop=True)
    return daily, bars_chosen


def classify_days(daily: pd.DataFrame, cfg, n_expected_bars: int) -> pd.DataFrame:
    """Compute boolean flags and the first-applicable exclusion reason.

    Separated from ``build_daily`` so it can be unit-tested on tiny synthetic frames.
    Needs columns: chosen_id, chosen_prev_share, chosen_expiry, n_missing_range,
    n_missing_window, xnys_session, xnys_early_close, vendor_degraded.
    """
    f = cfg.filters
    d = daily.copy()
    d["flag_no_prev_day"] = d["chosen_id"].isna()
    d["flag_no_chosen_bars"] = (d["n_missing_range"] + d["n_missing_window"]) >= n_expected_bars
    d["flag_cash_closed"] = ~d["xnys_session"]
    d["flag_early_close"] = d["xnys_early_close"]
    d["flag_vendor_degraded"] = d["vendor_degraded"]
    d["flag_range_incomplete"] = d["n_missing_range"] > f.max_missing_range_bars
    d["flag_window_incomplete"] = d["n_missing_window"] > f.max_missing_window_bars
    prev_chosen = d["chosen_id"].shift(1)
    d["flag_roll_new_contract"] = (d["chosen_id"] != prev_chosen) & d["chosen_id"].notna() & prev_chosen.notna()
    d["flag_roll_low_share"] = d["chosen_prev_share"] < cfg.contracts.roll_leader_min_share
    d["flag_chosen_expired"] = d["chosen_expiry"].notna() & (d["chosen_expiry"] <= d.index.to_series())

    # Which flags actually remove a day. The first two are structural: never optional.
    active = {
        "no_prev_day": True,
        "no_chosen_bars": True,
        "cash_closed": f.exclude_cash_closed,
        "early_close": f.exclude_early_close,
        "vendor_degraded": f.exclude_vendor_degraded,
        "range_incomplete": True,
        "window_incomplete": True,
        "roll_new_contract": f.exclude_roll_days,
        "roll_low_share": f.exclude_roll_days,
    }
    reason = pd.Series("", index=d.index, dtype="object")
    for name in reversed(REASON_PRIORITY):  # assign lowest priority first so higher overwrites
        if active[name]:
            reason = reason.mask(d[f"flag_{name}"], name)
    d["exclude_reason"] = reason
    d["tradable"] = reason == ""
    return d
