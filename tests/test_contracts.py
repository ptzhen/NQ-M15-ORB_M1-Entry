import datetime as dt

import pandas as pd
import pytest

from orb.data.contracts import resolve_expiry, third_friday
from orb.data.loader import classify_symbols


def test_third_friday():
    assert third_friday(2020, 3) == dt.date(2020, 3, 20)
    assert third_friday(2010, 6) == dt.date(2010, 6, 18)
    assert third_friday(2025, 12) == dt.date(2025, 12, 19)


@pytest.mark.parametrize(
    "symbol, first_seen, expected",
    [
        ("NQH0", "2019-03-15", dt.date(2020, 3, 20)),   # symbol reused: this is Mar-2020, not Mar-2010
        ("NQM0", "2010-06-06", dt.date(2010, 6, 18)),   # the very first contract in the file
        ("NQH1", "2010-06-06", dt.date(2011, 3, 18)),
        ("NQZ9", "2018-09-20", dt.date(2019, 12, 20)),
        ("NQH6", "2025-03-21", dt.date(2026, 3, 20)),
    ],
)
def test_expiry_resolves_decade_from_first_seen(symbol, first_seen, expected):
    assert resolve_expiry(symbol, pd.Timestamp(first_seen, tz="UTC")) == expected


def test_classify_symbols_drops_spreads_and_other_products(cfg):
    s = pd.Series(["NQH4", "NQM0-NQU0", "MGCZ5", "NQZ9", "NQM3-NQU3"])
    kind = classify_symbols(s, cfg.data.outright_regex).tolist()
    assert kind == ["outright", "spread", "other", "outright", "spread"]
