"""Stage 8 safeguards. These tests never read the holdout: they exercise the verdict rules (D8) on synthetic numbers and
the refusal paths of the one-shot final run."""

import argparse
import copy
import json

import pandas as pd
import pytest

from orb.analysis.final import Period, verdict
from orb.cli import cmd_final
from orb.data.splits import HoldoutLockedError


def _period(label, exp, lo, hi, exp_2x):
    stats = {"expectancy_r": (exp, lo, hi)}
    cost = {2.0: {"mean_r": exp_2x}}
    return Period(label, pd.DatetimeIndex([]), {}, pd.DataFrame(), stats, cost, pd.DataFrame())


VOL_OK, DIR_NO = {"supported": False, "testable": True}, {"supported": False}


def v(dev=(-0.05, -0.08, 0.02), ho=(0.08, 0.02, 0.14), ho_2x=0.04):
    return verdict(_period("dev", *dev, 0.0), _period("ho", *ho, ho_2x), VOL_OK, DIR_NO)


def test_edge_requires_all_three_conditions():
    assert v()["edge"] and v()["primary"] == "positive and distinguishable" and v()["failed"] == []


def test_holdout_interval_touching_zero_is_not_an_edge():
    r = v(ho=(0.05, -0.01, 0.11))
    assert not r["edge"] and r["primary"] == "not distinguishable from zero" and r["failed"][0].startswith("(a)")


def test_negative_at_double_costs_is_not_an_edge():
    r = v(ho_2x=-0.01)
    assert not r["edge"] and any(f.startswith("(b)") for f in r["failed"])


def test_a_contradicting_development_result_blocks_the_claim():
    r = v(dev=(-0.05, -0.09, -0.02))                                  # development interval entirely below zero
    assert not r["edge"] and any(f.startswith("(c)") for f in r["failed"]) and r["a"] and r["b"]


def test_negative_and_distinguishable_holdout():
    r = v(ho=(-0.06, -0.10, -0.02), ho_2x=-0.1)
    assert r["primary"] == "negative and distinguishable" and not r["edge"]


def _cfg_with_tmp_paths(cfg, tmp_path, lock_exists):
    c = copy.deepcopy(cfg)
    c.paths.final_lock = str((tmp_path / "lock.json").relative_to(tmp_path))
    c.paths.holdout_log = "holdout.log"
    c.root = tmp_path
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "frozen_vol_thresholds.json").write_text(json.dumps({"low_mid_threshold": 0.01, "mid_high_threshold": 0.014}))
    if lock_exists:
        (tmp_path / "lock.json").write_text(json.dumps({"summary": {}}))
    return c


def test_second_final_run_is_refused(cfg, tmp_path):
    c = _cfg_with_tmp_paths(cfg, tmp_path, lock_exists=True)
    with pytest.raises(SystemExit, match="REFUSED"):
        cmd_final(c, True, argparse.Namespace(dry_run=False, reproduce=False))
    assert not (tmp_path / "holdout.log").exists()                    # refused before anything was read or logged


def test_reproduce_before_the_final_run_is_refused(cfg, tmp_path):
    c = _cfg_with_tmp_paths(cfg, tmp_path, lock_exists=False)
    with pytest.raises(SystemExit, match="Nothing to reproduce"):
        cmd_final(c, True, argparse.Namespace(dry_run=False, reproduce=True))


def test_final_run_cannot_read_the_holdout_without_the_unlock_switch(cfg, tmp_path):
    c = _cfg_with_tmp_paths(cfg, tmp_path, lock_exists=False)
    with pytest.raises(HoldoutLockedError):
        cmd_final(c, True, argparse.Namespace(dry_run=False, reproduce=False))
    assert not (tmp_path / "holdout.log").exists()                    # a refused access is not logged as an access
    assert not (tmp_path / "lock.json").exists()                      # and leaves no lock behind
