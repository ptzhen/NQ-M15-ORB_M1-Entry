"""Contract metadata: month, expiry and lifespan of each NQ future.

Databento symbols carry a single-digit year (``NQH4``), so the decade has to be
inferred. A contract is listed roughly 15 months before expiry, so the *first date
we see it trade* pins down the decade unambiguously: the expiry year is the first
year >= the first-seen year whose last digit matches.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

MONTH_CODES = {c: i + 1 for i, c in enumerate("FGHJKMNQUVXZ")}


def third_friday(year: int, month: int) -> dt.date:
    """NQ stops trading on the third Friday of its expiry month."""
    first = dt.date(year, month, 1)
    first_friday = first + dt.timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + dt.timedelta(days=14)


def resolve_expiry(symbol: str, first_seen: pd.Timestamp) -> dt.date:
    """Expiry date of e.g. ``NQH0`` first seen trading on ``first_seen``.

    WHY first-seen: ``NQH0`` first trades in Mar-2019 (-> Mar 2020), whereas a
    contract named ``NQM0`` that is trading in Jun-2010 expires Jun 2010. Taking the
    first year >= first_seen.year with the right last digit resolves both.
    """
    month = MONTH_CODES[symbol[2]]
    digit = int(symbol[3])
    year = first_seen.year + ((digit - first_seen.year) % 10)
    expiry = third_friday(year, month)
    if expiry < first_seen.date():  # same-year digit match but the month already passed
        expiry = third_friday(year + 10, month)
    return expiry


def build_contract_table(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per instrument_id: symbol, first/last bar (UTC), expiry date."""
    g = bars.groupby("instrument_id")
    t = pd.DataFrame(
        {
            "symbol": g["symbol"].first(),
            "n_symbols": g["symbol"].nunique(),
            "first_ts": g["ts_event"].min(),
            "last_ts": g["ts_event"].max(),
            "rows": g.size(),
        }
    )
    t["expiry"] = [resolve_expiry(s, ts) for s, ts in zip(t["symbol"], t["first_ts"])]
    t["expiry"] = pd.to_datetime(t["expiry"])
    return t
