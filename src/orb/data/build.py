"""Stage 2 pipeline: raw Databento file -> validated, ET-aligned, daily-classified data."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from orb.config import resolve
from orb.data.contracts import build_contract_table
from orb.data.daily import add_et_columns, build_daily
from orb.data.loader import load_raw_nq, load_vendor_conditions
from orb.data.sessions import load_xnys
from orb.data.validation import assert_integrity, integrity_report


@dataclass
class DataBundle:
    bars: pd.DataFrame          # all NQ outright bars, full Globex session, with ET columns
    contracts: pd.DataFrame     # one row per instrument_id
    daily: pd.DataFrame         # one row per ET date with RTH data
    bars_chosen: pd.DataFrame   # RTH bars of the chosen contract, all dates
    xnys: pd.DataFrame          # NYSE sessions
    vendor: pd.Series           # Databento per-date condition (available/degraded)
    load_stats: dict
    integrity: dict


def build_data(cfg, use_cache: bool = True) -> DataBundle:
    bars, load_stats = load_raw_nq(cfg, use_cache=use_cache)

    integrity = integrity_report(bars, cfg.data.tick_size)
    assert_integrity(integrity)  # raises on anything that would silently corrupt a backtest

    contracts = build_contract_table(bars)
    bars = add_et_columns(bars, cfg.session.timezone)

    start, end = bars["date"].min(), bars["date"].max()
    xnys = load_xnys(str(start.date()), str(end.date()), cfg.session.timezone, cfg.session.calendar)

    vendor = load_vendor_conditions(cfg)
    daily, bars_chosen = build_daily(bars, contracts, xnys, vendor, cfg)

    out = resolve(cfg, cfg.paths.processed_dir)
    out.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(out / "daily.parquet")
    bars_chosen.to_parquet(out / "bars_chosen.parquet")
    contracts.to_parquet(out / "contracts.parquet")
    return DataBundle(bars, contracts, daily, bars_chosen, xnys, vendor, load_stats, integrity)
