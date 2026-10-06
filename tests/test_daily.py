"""Contract selection, day classification and holdout guard on tiny synthetic data.

The key property for this stage: the contract traded on day D is a function of data from
days < D only. ``test_selection_ignores_same_day_volume`` proves it by changing day D's
volumes and checking the choice does not move.
"""

import copy

import pandas as pd
import pytest

from conftest import make_day_bars, xnys_for
from orb.data.daily import add_et_columns, build_daily
from orb.data.splits import HoldoutLockedError, load_bars_chosen, load_daily

D1, D2, D3, D4 = "2023-06-05", "2023-06-06", "2023-06-07", "2023-06-08"
NO_VENDOR = pd.Series(dtype=object)


def _build(cfg, contracts_tbl, frames, xnys=None, vendor=NO_VENDOR):
    bars = add_et_columns(pd.concat(frames, ignore_index=True), "America/New_York")
    xnys = xnys if xnys is not None else xnys_for([D1, D2, D3, D4])
    return build_daily(bars, contracts_tbl, xnys, vendor, cfg)


def _frames(vol_d2_a=1000, vol_d2_b=5000):
    """Day 1: contract 1 leads. Day 2: contract 2 overtakes. Days 3-4: contract 2 leads."""
    f = []
    f += [make_day_bars(D1, 1, "NQM3", 5000), make_day_bars(D1, 2, "NQU3", 100)]
    f += [make_day_bars(D2, 1, "NQM3", vol_d2_a), make_day_bars(D2, 2, "NQU3", vol_d2_b)]
    for d in (D3, D4):
        f += [make_day_bars(d, 1, "NQM3", 50), make_day_bars(d, 2, "NQU3", 5000)]
    return f


def test_chosen_contract_is_previous_days_leader(cfg, contracts_tbl):
    daily, _ = _build(cfg, contracts_tbl, _frames())
    ts = pd.Timestamp
    assert pd.isna(daily.loc[ts(D1), "chosen_id"])           # no prior day
    assert daily.loc[ts(D2), "leader_id"] == 2                # contract 2 leads on day 2 ...
    assert daily.loc[ts(D2), "chosen_id"] == 1                # ... but we only knew day 1 -> trade contract 1
    assert daily.loc[ts(D3), "chosen_id"] == 2                # day 3 follows day 2's leader


def test_selection_ignores_same_day_volume(cfg, contracts_tbl):
    """No lookahead: rewriting day 2's volumes must not change which contract day 2 trades."""
    a, _ = _build(cfg, contracts_tbl, _frames(vol_d2_a=1000, vol_d2_b=5000))
    b, _ = _build(cfg, contracts_tbl, _frames(vol_d2_a=9_000_000, vol_d2_b=1))
    assert a.loc[D2, "chosen_id"] == b.loc[D2, "chosen_id"] == 1
    # ... while day 3's choice DOES respond to day 2 (past information is used)
    assert a.loc[D3, "chosen_id"] != b.loc[D3, "chosen_id"]


def test_roll_days_flagged_and_excluded(cfg, contracts_tbl):
    daily, _ = _build(cfg, contracts_tbl, _frames())
    assert daily.loc[D3, "flag_roll_new_contract"]            # first day on the new contract
    assert daily.loc[D3, "exclude_reason"] == "roll_new_contract"
    assert not daily.loc[D3, "tradable"]
    assert daily.loc[D4, "tradable"]                          # clean day after the roll


def test_first_day_has_no_prior_and_is_excluded(cfg, contracts_tbl):
    daily, _ = _build(cfg, contracts_tbl, _frames())
    assert daily.loc[D1, "exclude_reason"] == "no_prev_day"


def test_missing_bar_in_opening_range_excludes_day(cfg, contracts_tbl):
    f = _frames()
    f[-2] = make_day_bars(D4, 1, "NQM3", 50)  # irrelevant contract
    f[-1] = make_day_bars(D4, 2, "NQU3", 5000, skip=["09:40"])  # chosen on D4 is contract 2
    daily, _ = _build(cfg, contracts_tbl, f)
    assert daily.loc[D4, "n_missing_range"] == 1
    assert daily.loc[D4, "exclude_reason"] == "range_incomplete"


def test_missing_bar_in_trading_window_excludes_day(cfg, contracts_tbl):
    f = _frames()
    f[-1] = make_day_bars(D4, 2, "NQU3", 5000, skip=["12:00"])
    daily, _ = _build(cfg, contracts_tbl, f)
    assert daily.loc[D4, "n_missing_window"] == 1
    assert daily.loc[D4, "exclude_reason"] == "window_incomplete"


def test_early_close_and_cash_closed_and_vendor_degraded(cfg, contracts_tbl):
    xn = xnys_for([D1, D2, D3, D4], early=[D4])
    xn = xn.drop(pd.Timestamp(D3))                            # D3: NYSE closed (CME short session)
    vendor = pd.Series({pd.Timestamp(D2): "degraded"})
    daily, _ = _build(cfg, contracts_tbl, _frames(), xnys=xn, vendor=vendor)
    assert daily.loc[D2, "exclude_reason"] == "vendor_degraded"
    assert daily.loc[D3, "exclude_reason"] == "cash_closed"   # outranks the roll flag on the same day
    assert daily.loc[D4, "exclude_reason"] == "early_close"


def test_filters_can_be_switched_off(cfg, contracts_tbl):
    cfg2 = copy.deepcopy(cfg)
    cfg2.filters.exclude_roll_days = False
    daily, _ = _build(cfg2, contracts_tbl, _frames())
    assert daily.loc[D3, "flag_roll_new_contract"]            # still flagged and reported
    assert daily.loc[D3, "tradable"]                          # but no longer removed


def test_chosen_bars_only_contain_chosen_contract(cfg, contracts_tbl):
    daily, bars = _build(cfg, contracts_tbl, _frames())
    for d, grp in bars.groupby("date"):
        assert (grp["instrument_id"] == daily.loc[d, "chosen_id"]).all()
    assert pd.Timestamp(D1) not in set(bars["date"])           # no chosen contract -> no bars


def test_holdout_guard(cfg, tmp_path, monkeypatch):
    cfg2 = copy.deepcopy(cfg)
    cfg2.root = tmp_path
    cfg2.paths.processed_dir = "proc"
    cfg2.paths.holdout_log = "logs/holdout.log"
    (tmp_path / "proc").mkdir()
    idx = pd.DatetimeIndex(["2023-12-29", "2024-01-02", "2025-05-05"])
    pd.DataFrame({"x": [1, 2, 3]}, index=idx).to_parquet(tmp_path / "proc" / "daily.parquet")
    pd.DataFrame({"date": idx, "x": [1, 2, 3]}).to_parquet(tmp_path / "proc" / "bars_chosen.parquet")

    monkeypatch.delenv("ORB_UNLOCK_HOLDOUT", raising=False)
    assert list(load_daily(cfg2, "dev").index) == [pd.Timestamp("2023-12-29")]
    assert len(load_bars_chosen(cfg2, "dev")) == 1
    for part in ("holdout", "all"):
        with pytest.raises(HoldoutLockedError):
            load_daily(cfg2, part)
    assert not (tmp_path / "logs").exists()                    # a refused access is not an access

    monkeypatch.setenv("ORB_UNLOCK_HOLDOUT", "yes")
    assert len(load_daily(cfg2, "holdout", purpose="unit test")) == 2
    log = (tmp_path / "logs" / "holdout.log").read_text()
    assert "unit test" in log and "holdout" in log
