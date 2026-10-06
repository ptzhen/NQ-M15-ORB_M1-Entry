"""Diagnostics that *test* the data-handling assumptions rather than assume them.

Each function answers one question the data-quality report must be able to defend:
is the timezone right, are rolls handled, does the calendar agree with the bars.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from orb.config import hhmm_to_minutes


def timezone_proof(bars: pd.DataFrame, cfg) -> dict:
    """Show that converted timestamps put the volume spike at 09:30 ET in every season.

    Sums volume across contracts per minute. If the UTC->ET conversion (or the
    "bar-open label" assumption) were wrong, the spike would sit at 09:00/10:00 ET or
    at 09:30 only in one half of the year.
    """
    open_min = hhmm_to_minutes(cfg.session.cash_open)
    per_min = bars.groupby("ts_event", sort=True)["volume"].sum().to_frame("volume")
    ts_et = per_min.index.tz_convert(cfg.session.timezone)
    per_min["date"] = ts_et.tz_localize(None).normalize()
    per_min["mod"] = (ts_et.hour * 60 + ts_et.minute).astype(int)
    # ET wall clock minus UTC wall clock: -4h in daylight time (EDT), -5h in standard time (EST)
    offset = ts_et.tz_localize(None) - per_min.index.tz_convert("UTC").tz_localize(None)
    per_min["is_dst"] = np.asarray(offset == pd.Timedelta(hours=-4))
    per_min["utc_hour"] = per_min.index.hour

    # Average total volume per ET minute-of-day, per season, over days with any data
    # Denominator = weekdays with RTH data. Counting every ET date would include Sunday-evening
    # "dates" (which only hold overnight bars) and dilute the averages by ~20%.
    in_rth = (per_min["mod"] >= open_min) & (per_min["mod"] < hhmm_to_minutes(cfg.session.cash_close))
    days = per_min[in_rth].groupby("is_dst")["date"].nunique()
    prof = per_min.groupby(["is_dst", "mod"])["volume"].sum().unstack(0)
    prof = prof / days  # absent minute counts as zero volume
    prof.columns = ["standard_time_EST" if not c else "daylight_time_EDT" for c in prof.columns]

    # Day-level spike ratio: volume(09:30) / volume(09:29)
    two = per_min[per_min["mod"].isin([open_min - 1, open_min])].pivot_table(index="date", columns="mod", values="volume", aggfunc="sum")
    ratio = (two[open_min] / two[open_min - 1]).dropna()
    ratio.index = pd.DatetimeIndex(ratio.index)
    # US and EU change clocks on different dates: Mar 8-31 and Oct 25-Nov 7 are the windows where a
    # wrong (e.g. EU-rule or fixed-offset) conversion would break while a correct one holds.
    mis = ((ratio.index.month == 3) & (ratio.index.day >= 8)) | ((ratio.index.month == 10) & (ratio.index.day >= 25)) | ((ratio.index.month == 11) & (ratio.index.day <= 7))

    # UTC hour in which the 09:30 ET bar falls, by season: 13 vs 14 proves DST is applied
    open_bars = per_min[per_min["mod"] == open_min]
    utc_hours = open_bars.groupby("is_dst")["utc_hour"].agg(lambda s: s.value_counts().to_dict())

    return {
        "profile": prof,
        "ratio": ratio,
        "share_ratio_gt3_all": float((ratio > 3).mean()),
        "share_ratio_gt3_dst_mismatch_windows": float((ratio[mis] > 3).mean()),
        "n_mismatch_days": int(mis.sum()),
        "median_ratio": float(ratio.median()),
        "open_bar_utc_hours": {("EDT" if k else "EST"): v for k, v in utc_hours.items()},
        "avg_vol_0929": float(prof.loc[open_min - 1].mean()),
        "avg_vol_0930": float(prof.loc[open_min].mean()),
    }


def roll_table(daily: pd.DataFrame, contracts: pd.DataFrame) -> pd.DataFrame:
    """One row per change of the day-leader contract, with how contested the roll was."""
    d = daily.copy()
    d["prev_leader"] = d["leader_id"].shift(1)
    ch = d[(d["leader_id"] != d["prev_leader"]) & d["prev_leader"].notna()]
    out = pd.DataFrame(
        {
            "from": ch["prev_leader"].astype(int).map(contracts["symbol"]),
            "to": ch["leader_id"].astype(int).map(contracts["symbol"]),
            "new_leader_share": ch["leader_share"],
        }
    )
    out["old_contract_expiry"] = ch["prev_leader"].astype(int).map(contracts["expiry"])
    out["calendar_days_to_old_expiry"] = (out["old_contract_expiry"] - out.index).dt.days
    return out


def calendar_crosscheck(daily: pd.DataFrame, xnys: pd.DataFrame, cfg) -> dict:
    """Does the NYSE calendar agree with what the bars say about short sessions?

    ``data_short_session`` = the last RTH bar across ALL contracts is before 14:00 ET.
    """
    data_short = daily["last_bar_min_any"] < 14 * 60
    cal_short = ~daily["xnys_session"] | daily["xnys_early_close"]
    both = data_short & cal_short
    sessions_in_data_range = xnys.loc[daily.index.min() : daily.index.max()]
    missing_sessions = sessions_in_data_range.index.difference(daily.index)
    return {
        "dates_with_rth_data": int(len(daily)),
        "xnys_sessions_in_range": int(len(sessions_in_data_range)),
        "xnys_sessions_without_any_data": [str(x.date()) for x in missing_sessions],
        "data_dates_not_xnys_session": int((~daily["xnys_session"]).sum()),
        "xnys_early_close_dates_in_data": int(daily["xnys_early_close"].sum()),
        "data_short_session_dates": int(data_short.sum()),
        "short_in_data_and_calendar": int(both.sum()),
        "short_in_data_not_calendar": [str(x.date()) for x in daily.index[data_short & ~cal_short]],
        "calendar_short_not_short_in_data": [str(x.date()) for x in daily.index[cal_short & ~data_short]],
    }


def chosen_contract_diagnostics(daily: pd.DataFrame) -> dict:
    """How good is 'yesterday's leader' as today's contract? (Same-day share is diagnostic only.)"""
    d = daily[daily["chosen_id"].notna()]
    roll = d["flag_roll_new_contract"] | d["flag_roll_low_share"]
    q = [0.01, 0.05, 0.5]
    return {
        "days": int(len(d)),
        "chosen_equals_same_day_leader": float(d["chosen_is_day_leader"].astype(bool).mean()),
        "n_mismatch_days": int((~d["chosen_is_day_leader"].astype(bool)).sum()),
        "mismatch_days_flagged_as_roll": int((~d["chosen_is_day_leader"].astype(bool) & roll).sum()),
        "day_share_quantiles_all": d["chosen_day_share"].quantile(q).round(3).to_dict(),
        "day_share_quantiles_roll_days": d.loc[roll, "chosen_day_share"].quantile(q).round(3).to_dict(),
        "day_share_quantiles_non_roll_days": d.loc[~roll, "chosen_day_share"].quantile(q).round(3).to_dict(),
        "day_share_min_non_roll_days": float(d.loc[~roll, "chosen_day_share"].min()),
        "n_unflagged_mismatch_days": int((~d["chosen_is_day_leader"].astype(bool) & ~roll).sum()),
        "n_roll_flagged": int(roll.sum()),
        "chosen_expired_days": int(d["flag_chosen_expired"].sum()),
    }
