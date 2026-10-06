"""Trading-calendar facts from the NYSE calendar (exchange_calendars, XNYS).

The ORB is defined on the *US cash open* at 09:30 ET. CME equity futures trade on
days the cash market is closed (MLK Day, Memorial Day, ...) with a short session
and no real 09:30 open, and on half-days (Black Friday, Dec 24, Jul 3) the cash
market closes at 13:00. Neither day is a valid ORB day, so we need the cash
calendar, not just "were there bars".
"""

from __future__ import annotations

import pandas as pd


def load_xnys(start: str, end: str, tz: str, calendar: str = "XNYS") -> pd.DataFrame:
    """One row per NYSE session in [start, end]: ``close_et`` and ``early_close``."""
    import exchange_calendars as xc

    cal = xc.get_calendar(calendar, start=pd.Timestamp(start) - pd.Timedelta(days=10), end=pd.Timestamp(end) + pd.Timedelta(days=10))
    sessions = cal.sessions_in_range(start, end)
    closes = cal.closes.loc[sessions].dt.tz_convert(tz)
    out = pd.DataFrame({"close_et": closes})
    out.index = pd.DatetimeIndex(sessions.tz_localize(None) if sessions.tz is not None else sessions, name="date")
    out["early_close"] = (closes.dt.hour * 60 + closes.dt.minute).to_numpy() < 16 * 60
    return out
