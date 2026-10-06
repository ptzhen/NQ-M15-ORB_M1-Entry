"""The 09:30 ET bar must map to the right UTC hour in every season, incl. DST edges."""

import pandas as pd
import pytest

from orb.data.daily import add_et_columns


def _et_of(utc: str) -> pd.DataFrame:
    df = pd.DataFrame({"ts_event": pd.to_datetime([utc], utc=True)})
    return add_et_columns(df, "America/New_York")


@pytest.mark.parametrize(
    "utc, expected_mod, expected_date",
    [
        ("2023-03-10 14:30", 9 * 60 + 30, "2023-03-10"),  # Fri before US spring-forward: EST (UTC-5)
        ("2023-03-13 13:30", 9 * 60 + 30, "2023-03-13"),  # Mon after: EDT (UTC-4)
        ("2023-11-03 13:30", 9 * 60 + 30, "2023-11-03"),  # Fri before US fall-back: EDT
        ("2023-11-06 14:30", 9 * 60 + 30, "2023-11-06"),  # Mon after: EST
        ("2023-03-20 13:30", 9 * 60 + 30, "2023-03-20"),  # US already on DST while EU switches later
        ("2010-06-07 13:30", 9 * 60 + 30, "2010-06-07"),  # first day of the dataset
    ],
)
def test_open_bar_maps_to_0930_et(utc, expected_mod, expected_date):
    r = _et_of(utc).iloc[0]
    assert r["mod"] == expected_mod
    assert r["date"] == pd.Timestamp(expected_date)


def test_et_date_differs_from_utc_date_late_evening():
    """Evening Globex bars cross midnight UTC before midnight ET: the ET date must lag the UTC date."""
    r = _et_of("2023-06-05 02:00").iloc[0]  # Monday 02:00 UTC == Sunday 22:00 EDT
    assert r["date"] == pd.Timestamp("2023-06-04") and r["mod"] == 22 * 60
    r = _et_of("2023-06-04 22:00").iloc[0]  # Sunday 22:00 UTC == Sunday 18:00 EDT (Globex reopen)
    assert r["date"] == pd.Timestamp("2023-06-04") and r["mod"] == 18 * 60
