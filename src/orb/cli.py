"""Command line entry point: ``python -m orb <command>``.

``data`` builds and validates the dataset and writes the data-quality report.
``run`` executes every implemented stage in order (this grows as stages are added).
"""

from __future__ import annotations

import argparse

import numpy as np

from orb.config import load_config


def cmd_data(cfg, use_cache: bool) -> None:
    from orb.data.build import build_data
    from orb.reports.data_quality import build_report

    bundle = build_data(cfg, use_cache=use_cache)
    path = build_report(cfg, bundle)
    d = bundle.daily
    print(f"dates with RTH data: {len(d):,}   tradable: {int(d['tradable'].sum()):,}")
    print(f"report: {path}")


def cmd_signals(cfg, use_cache: bool) -> None:
    from orb.reports.signal_summary import build_signal_report

    print(f"report: {build_signal_report(cfg)}")


def cmd_execution(cfg, use_cache: bool) -> None:
    from orb.reports.execution_report import build_execution_report

    print(f"report: {build_execution_report(cfg)}")


def cmd_backtest(cfg, use_cache: bool) -> None:
    from orb.reports.backtest_report import build_backtest_report

    print(f"report: {build_backtest_report(cfg)}")


def cmd_validation(cfg, use_cache: bool) -> None:
    from orb.reports.validation_report import build_validation_report

    print(f"report: {build_validation_report(cfg)}")


def cmd_robustness(cfg, use_cache: bool) -> None:
    from orb.reports.robustness_report import build_robustness_report

    print(f"report: {build_robustness_report(cfg)}")


def cmd_final(cfg, use_cache: bool, args=None) -> None:
    """Stage 8. The ONE place the holdout is read for a decision (D8). Guarded three ways: the explicit unlock environment variable,
    the access log, and a lock file that makes a second run a refusal unless ``--reproduce`` is given."""
    import datetime as dt
    import json
    import os
    import subprocess

    import pandas as pd

    from orb.analysis.backtest import run_backtest
    from orb.analysis.final import compute_final, load_thresholds
    from orb.config import resolve
    from orb.data.splits import load_bars_chosen, load_daily
    from orb.reports.final_report import render_final

    args = args or argparse.Namespace(dry_run=False, reproduce=False)
    thresholds = load_thresholds(cfg)
    out_root = resolve(cfg, cfg.paths.output_dir)

    if args.dry_run:                                   # development data only: pseudo-holdout inside the development period
        purpose = "stage 8 DRY RUN on development data only (pseudo-holdout 2021+)"
        daily, bars = load_daily(cfg, "dev", purpose=purpose), load_bars_chosen(cfg, "dev", purpose=purpose)
        res = compute_final(cfg, bars, daily, "2021-01-01", thresholds)
        print(f"DRY RUN report: {render_final(cfg, res, out_root / 'final_dryrun', 'DRY RUN')}")
        return

    lock = resolve(cfg, cfg.paths.final_lock)
    if lock.exists() and not args.reproduce:
        raise SystemExit(f"REFUSED: the final holdout run already happened ({lock.name}). Use `--reproduce` to re-derive the same numbers for verification; "
                         "no decision may be taken from a reproduction (D8).")
    if args.reproduce and not lock.exists():
        raise SystemExit("Nothing to reproduce: the final run has not happened yet.")
    reproduce = lock.exists()
    purpose = "stage 8 reproduction of the final run (verification only, no decisions)" if reproduce else "stage 8 FINAL holdout run (D8), the one and only decision run"
    daily, bars = load_daily(cfg, "all", purpose=purpose), load_bars_chosen(cfg, "all", purpose=purpose)   # raises HoldoutLockedError unless unlocked; logs the access
    res = compute_final(cfg, bars, daily, cfg.splits.holdout_start, thresholds)

    ref = run_backtest(cfg, "dev", purpose="stage 8 integrity check: development trades must be unchanged by loading the holdout").trades
    pd.testing.assert_series_equal(res["dev"].trades[1.0]["pnl_usd_nq"], ref["pnl_usd_nq"])

    mode = "REPRODUCTION" if reproduce else "FINAL"
    path = render_final(cfg, res, out_root / "final", mode)
    ho, vd = res["ho"], res["verdict"]
    summary = {"holdout_trades": int(ho.stats["trades"]), "holdout_expectancy_r": round(ho.stats["expectancy_r"][0], 6),
               "holdout_expectancy_interval": [round(ho.stats["expectancy_r"][1], 6), round(ho.stats["expectancy_r"][2], 6)],
               "holdout_total_usd_nq": round(ho.stats["total_usd_nq"], 2), "direction_p": round(res["dir"]["p_value"], 6),
               "high_vol_trades": int(res["vol"]["high"]["n"]), "verdict_primary": vd["primary"], "edge_established": bool(vd["edge"])}
    if reproduce:
        prior = json.loads(lock.read_text(encoding="utf-8"))["summary"]
        assert prior == summary, f"REPRODUCTION MISMATCH: original {prior} vs now {summary}"
        print("REPRODUCED: every key number matches the original final run.")
    else:
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=cfg.root).stdout.strip()
        except Exception:
            head = "unknown"
        lock.write_text(json.dumps({"completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "code_commit": head,
                                    "note": "The one and only decision run on the holdout (D8). Further runs must use --reproduce.", "summary": summary}, indent=2), encoding="utf-8")
    print(f"{mode} report: {path}")


STAGES = {"data": cmd_data, "signals": cmd_signals, "execution": cmd_execution, "backtest": cmd_backtest, "validation": cmd_validation,
          "robustness": cmd_robustness}


def resolve_lock_exists(cfg) -> bool:
    from orb.config import resolve
    return resolve(cfg, cfg.paths.final_lock).exists()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="orb", description=__doc__)
    p.add_argument("command", choices=[*STAGES, "final", "run"])
    p.add_argument("--config", default=None, help="path to config YAML (default: config/config.yaml)")
    p.add_argument("--no-cache", action="store_true", help="re-parse the raw file instead of using the interim cache")
    p.add_argument("--dry-run", action="store_true", help="final: run the pipeline on development data only (pseudo-holdout), never touching the holdout")
    p.add_argument("--reproduce", action="store_true", help="final: re-derive the already-completed final run for verification (logged; no decisions)")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    np.random.seed(cfg.seed)  # every stochastic step also takes cfg.seed explicitly
    names = [*STAGES] if args.command == "run" else [args.command]
    for name in names:
        STAGES[name](cfg, use_cache=not args.no_cache) if name != "final" else cmd_final(cfg, not args.no_cache, args)
    if args.command == "run":
        import os
        if os.environ.get("ORB_UNLOCK_HOLDOUT") == "yes":
            args.reproduce = resolve_lock_exists(cfg)
            cmd_final(cfg, not args.no_cache, args)
        else:
            print("final stage skipped: the holdout is locked (set ORB_UNLOCK_HOLDOUT=yes to re-derive the final report in reproduction mode).")

if __name__ == "__main__":
    main()
