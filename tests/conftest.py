"""Shared fixtures: the real config plus small synthetic one-minute bar builders."""

import numpy as np
import pandas as pd
import pytest

from orb.config import load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config()


def make_day_bars(date: str, instrument_id: int, symbol: str, volume: int, tz="America/New_York",
                  start="09:30", end="15:59", skip=(), base_price=15000.0) -> pd.DataFrame:
    """One contract's RTH minute bars for one ET date. ``skip`` is a list of 'HH:MM' with no bar."""
    ts_et = pd.date_range(f"{date} {start}", f"{date} {end}", freq="min", tz=tz)
    ts_et = ts_et[~ts_et.strftime("%H:%M").isin(list(skip))]
    n = len(ts_et)
    price = base_price + np.arange(n) * 0.25
    return pd.DataFrame(
        {
            "ts_event": ts_et.tz_convert("UTC"),
            "instrument_id": instrument_id,
            "symbol": symbol,
            "open": price, "high": price + 1.0, "low": price - 1.0, "close": price + 0.25,
            "volume": volume,
        }
    )


@pytest.fixture
def contracts_tbl():
    t = pd.DataFrame(
        {"symbol": ["NQM3", "NQU3"], "expiry": pd.to_datetime(["2023-06-16", "2023-09-15"])},
        index=pd.Index([1, 2], name="instrument_id"),
    )
    return t


def xnys_for(dates, early=()):
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)), name="date")
    return pd.DataFrame({"early_close": [d in set(pd.to_datetime(list(early))) for d in idx]}, index=idx)
