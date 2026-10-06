"""Load the raw Databento file and keep only NQ outright contracts.

The download is a *parent-symbology* query (``NQ.FUT``), which means it contains
every listed instrument under that parent, not a tidy continuous series:

* NQ outrights (``NQH4``)             <- what we want
* NQ calendar spreads (``NQM0-NQU0``) <- different price scale, would corrupt any
                                         symbol-agnostic logic, so dropped
* MGC (micro gold)                    <- came along in the same query, irrelevant

Rows are kept per ``instrument_id`` and never merged across contracts here. Contract
symbols are reused every decade (``NQH0`` is both Mar-2010 and Mar-2020), so
``instrument_id`` is the only safe key.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from orb.config import resolve


# Bump when the cached content (filtering, statistics labels) changes. The cache is keyed on the raw file's hash AND this
# version, so code changes can never be hidden behind stale cached statistics.
CACHE_VERSION = 2


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def classify_symbols(symbols: pd.Series, outright_regex: str) -> pd.Series:
    """Label each row as 'outright', 'spread' or 'other' (e.g. MGC)."""
    is_nq = symbols.str.startswith("NQ")
    is_outright = symbols.str.match(outright_regex)
    out = pd.Series("other", index=symbols.index, dtype="object")
    out[is_nq & symbols.str.contains("-", regex=False)] = "spread"
    out[is_outright] = "outright"
    return out


def load_raw_nq(cfg, use_cache: bool = True) -> tuple[pd.DataFrame, dict]:
    """Return (NQ outright bars, load statistics).

    Bars have UTC ``ts_event`` (the bar OPEN time, confirmed in the data-quality
    report), ``instrument_id``, ``symbol``, OHLC as float and integer volume.
    The parse takes ~30s, so the filtered result is cached next to a sidecar that
    records the raw file's SHA-256; a different download invalidates the cache.
    """
    raw_path = resolve(cfg, cfg.paths.raw_file)
    if not raw_path.exists():
        raise FileNotFoundError(
            f"Raw data not found at {raw_path}. Download it from Databento "
            "(GLBX.MDP3, ohlcv-1m, NQ.FUT, 2010-06 to 2026-10) and extract the .dbn.zst "
            "into data/raw/. See README."
        )

    digest = sha256_file(raw_path)
    if digest != cfg.paths.raw_sha256:
        raise ValueError(
            f"Raw file SHA-256 mismatch: expected {cfg.paths.raw_sha256}, got {digest}. "
            "Results would not be reproducible; update config.paths.raw_sha256 only if "
            "you intend to analyse different data."
        )

    interim = resolve(cfg, cfg.paths.interim_dir)
    interim.mkdir(parents=True, exist_ok=True)
    cache_pq, cache_meta = interim / "nq_outrights.parquet", interim / "nq_outrights.meta.json"
    if use_cache and cache_pq.exists() and cache_meta.exists():
        meta = json.loads(cache_meta.read_text())
        if meta.get("sha256") == digest and meta.get("version") == CACHE_VERSION:
            return pd.read_parquet(cache_pq), meta["stats"]

    import databento as db  # imported lazily: heavy, and unneeded when the cache hits

    df = (
        db.DBNStore.from_file(raw_path)
        .to_df(price_type="float", pretty_ts=True, map_symbols=True)
        .reset_index()
    )
    df["symbol"] = df["symbol"].astype(str)
    kind = classify_symbols(df["symbol"], cfg.data.outright_regex)
    stats = {
        "rows_total": int(len(df)),
        "rows_outright": int((kind == "outright").sum()),
        "rows_spread": int((kind == "spread").sum()),
        "rows_other": int((kind == "other").sum()),
        "other_prefixes": sorted(set(s[:3] for s in df.loc[kind == "other", "symbol"].unique())),
        "ts_min_utc": str(df["ts_event"].min()),
        "ts_max_utc": str(df["ts_event"].max()),
        "sha256": digest,
    }
    bars = (
        df.loc[kind == "outright", ["ts_event", "instrument_id", "symbol", "open", "high", "low", "close", "volume"]]
        .sort_values(["ts_event", "instrument_id"], kind="stable")
        .reset_index(drop=True)
    )
    bars["volume"] = bars["volume"].astype("int64")
    bars["instrument_id"] = bars["instrument_id"].astype("int64")
    bars.to_parquet(cache_pq)
    cache_meta.write_text(json.dumps({"sha256": digest, "version": CACHE_VERSION, "stats": stats}, indent=2))
    return bars, stats


def load_vendor_conditions(cfg) -> pd.Series:
    """Databento's per-date data-quality status ('available' / 'degraded').

    WHY use it: the vendor knows about outages and gaps on its side. Ignoring its own
    ledger would mean trusting days it has told us not to trust. Dates are UTC dates; an
    ET regular session (13:30-21:00 UTC) always lies inside one UTC date, so the ET
    trading date can be matched directly.
    """
    path = resolve(cfg, cfg.paths.condition_file)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; it ships in the same Databento download as the data.")
    rows = json.loads(path.read_text())
    s = pd.Series({pd.Timestamp(r["date"]): r["condition"] for r in rows}, name="condition")
    return s.sort_index()
