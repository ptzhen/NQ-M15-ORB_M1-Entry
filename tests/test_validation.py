import pandas as pd
import pytest

from orb.data.validation import assert_integrity, integrity_report


def _bars():
    return pd.DataFrame(
        {
            "ts_event": pd.to_datetime(["2023-06-05 13:30", "2023-06-05 13:31"], utc=True),
            "instrument_id": [1, 1], "symbol": ["NQM3", "NQM3"],
            "open": [15000.0, 15000.25], "high": [15001.0, 15001.0], "low": [14999.0, 15000.0],
            "close": [15000.25, 15000.5], "volume": [10, 12],
        }
    )


def test_clean_bars_pass():
    assert_integrity(integrity_report(_bars(), 0.25))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.assign(high=[15001.0, 14990.0]),                            # high < low
        lambda b: b.assign(close=[15000.1, 15000.5]),                            # off the 0.25 tick grid
        lambda b: pd.concat([b, b.iloc[[0]]], ignore_index=True),               # duplicate bar
        lambda b: b.assign(open=[-1.0, 15000.25]),                              # non-positive price
        lambda b: b.assign(symbol=["NQM3", "NQU3"]),                            # one id, two symbols
    ],
)
def test_structural_problems_abort(mutate):
    b = mutate(_bars().copy())
    with pytest.raises(ValueError):
        assert_integrity(integrity_report(b, 0.25))
