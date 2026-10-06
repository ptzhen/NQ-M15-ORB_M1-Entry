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


STAGES = {"data": cmd_data, "signals": cmd_signals, "execution": cmd_execution, "backtest": cmd_backtest}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="orb", description=__doc__)
    p.add_argument("command", choices=[*STAGES, "run"])
    p.add_argument("--config", default=None, help="path to config YAML (default: config/config.yaml)")
    p.add_argument("--no-cache", action="store_true", help="re-parse the raw file instead of using the interim cache")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    np.random.seed(cfg.seed)  # every stochastic step also takes cfg.seed explicitly
    for name in STAGES if args.command == "run" else [args.command]:
        STAGES[name](cfg, use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
