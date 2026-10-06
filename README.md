# NQ 15-minute Opening Range Breakout: does it have a real edge?

This project tests one simple trading idea as rigorously as possible, with the explicit goal of finding out
whether there is a genuine edge, **not** of producing a nice-looking equity curve. A good-looking result
is treated as suspicious until it survives the tests below.

> **Status: all 8 stages are complete.** Read the final write-up: [`docs/final/FINAL_REPORT.md`](docs/final/FINAL_REPORT.md).
>
> **Conclusion (by the rules fixed before the holdout was opened): no edge established.** Over 2010-2023 the rule lost about 0.05 R per trade after costs (95% interval -0.083 to -0.019 R) and none of 96 variants survived the multiple-testing
> correction. On the untouched 2024-2026 holdout it gained about +0.08 R per trade (interval +0.008 to +0.145), which clears zero narrowly but is not shown to come from the breakout itself and contradicts the 13.5-year history, so the evidence is *mixed*, not an edge.
> Stage reports: [data](docs/stage2_data_quality/data_quality_report.md), [signals](docs/stage3_signals/signal_summary.md), [execution and costs](docs/stage4_execution/execution_report.md), [backtest](docs/stage5_backtest/backtest_report.md),
> [validation](docs/stage6_validation/validation_report.md), [robustness](docs/stage7_robustness/robustness_report.md). Plan: [`docs/PLAN.md`](docs/PLAN.md). Owner decisions and pre-registrations: [`docs/DECISIONS.md`](docs/DECISIONS.md).

## The idea in plain English

When the US stock market opens at 09:30 New York time, prices often move wildly for a few minutes. The
**opening range** is the highest and lowest price reached in the first 15 minutes (09:30 to 09:45).
The strategy bets that when price breaks out of that range, it keeps going:

* if a 1-minute bar **closes above** the range high, buy at the next bar's open;
* if a 1-minute bar **closes below** the range low, sell short at the next bar's open;
* the stop-loss is the other side of the range, and the profit target is a multiple of the risk ("R");
* at most one trade a day, and everything is closed by 15:55.

The instrument is NQ, the E-mini Nasdaq-100 future (0.25-point ticks, $20 per point; the micro MNQ is $2 per point).

## Why this is hard to do honestly

Most backtests that look good are fooled by one of a handful of traps. The project is built around avoiding them:

| Trap | What we do |
|---|---|
| **Looking into the future** | Signals use only bars that had closed. Contracts are chosen from *yesterday's* volume. Unit tests deliberately inject lookahead and check that they fail. |
| **Fake profits from contract rolls** | Futures expire quarterly. Each trading day uses one contract with raw, unadjusted prices, so a roll can never create a gap. Roll days are flagged and excluded. |
| **Wrong clock** | Timestamps are UTC; we convert with the time-zone database (daylight saving aware) and prove the 09:30 volume spike lands on 09:30 in winter and summer. |
| **Trading on days you couldn't** | Holidays, half-days, days with missing minutes and days the data vendor flags as degraded are removed, and every removal is counted. |
| **Optimistic fills** | Costs and slippage are explicit, configurable assumptions. If a stop and a target are hit inside the same 1-minute bar, we assume the stop was hit first. |
| **Over-fitting** | Tune only on 2010-2023 using walk-forward tests. 2024-2026 is a holdout that is run exactly once, at the end, guarded in code. |
| **Data-mining luck** | Every variant tried is recorded, and results are adjusted for the number of trials (deflated Sharpe / reality check). |

## How to run

You need Python 3.12 and the Databento file this was built on (not included: it is licensed data).

```bash
pip install -r requirements.txt
pip install -e .
# put the Databento download's files (the .dbn.zst, condition.json, ...) into data/raw/
python -m orb data      # parse, validate, classify days, write the data-quality report
python -m orb signals   # generate signals on development data and write the signal report
python -m orb execution # simulate fills and costs on development data, write the execution report
python -m orb backtest  # full backtest on development data: trade log + breakdowns by year/regime/weekday/side
python -m orb validation # 96-cell parameter grid, in/out-of-sample split and walk-forward (protocol fixed in docs/DECISIONS.md D4)
python -m orb robustness # bootstrap, Monte Carlo, volatility-regime and direction tests, deflated Sharpe, reality check (docs/DECISIONS.md D6)
python -m orb final --dry-run # final pipeline on development data only (pseudo-holdout); safe
ORB_UNLOCK_HOLDOUT=yes python -m orb final --reproduce   # re-derive the (already completed) final run for verification; logged
python -m orb run       # every stage in order; the final stage is re-derived only if ORB_UNLOCK_HOLDOUT=yes
python -m pytest        # unit tests
```

All parameters are in [`config/config.yaml`](config/config.yaml). The raw file's SHA-256 is pinned there; a
different download is refused so that results stay reproducible. Random seeds are fixed in the same file.

Output lands in `outputs/` (git-ignored). The Stage 2 report is also published in `docs/stage2_data_quality/`.

## How to read the data-quality report

* **Section 3 (timezone):** the chart should show one sharp spike at 09:30 with the winter and summer lines on top of each other.
* **Section 4 (rolls):** lines show how much of each day's volume the contract we trade had; the orange dots are excluded roll days.
* **Section 5 (usable days):** a waterfall from all days to tradable days, with the reason for every removal.
* **Section 6-7:** the vendor's own quality ledger, and a cross-check of the NYSE holiday calendar against what the bars show.

## How to read the signal report

* It is descriptive only: how often signals fire, when, how wide the range is, and how much risk each trade carries. **No profit is computed at this stage.**
* Section 2 explains how the "no lookahead" claim is tested (a second, bar-by-bar implementation, garbage-in-the-future tests, and deliberately injected bugs).
* Section 6 lists edge cases the execution model must handle.

## How to read the execution report

* **R** is the risk on a trade (entry to stop). A result of +1R means you made as much as you risked. Expectancy is the average R per trade.
* Section 3 shows the same trades at 0x (no costs), 1x and 2x costs. If the edge only exists at 0x, it is not tradable.
* Section 5 shows how much the unavoidable 1-minute-bar guesses (stop-first, fill-on-touch) move the answer.
* The confidence interval shown is deliberately naive and too narrow; Stage 7 replaces it with a bootstrap.

## How to read the backtest report

* Section 2 splits the result into gross points, slippage and commission, in both NQ and MNQ dollars, so you can see what costs take.
* Section 4 (by year) shows each year's expectancy with its uncertainty bar. If the bars straddle zero, that year says nothing either way.
* Sections 5-6 (regime, weekday, side) are descriptive. Any pattern noticed there is a *hypothesis*, logged in `docs/DECISIONS.md`, and must be confirmed out of sample.
* The trade log itself is `outputs/backtest/trade_log_dev.csv` (not committed: it contains prices from licensed data).

## How to read the validation report

* The **heatmaps** show expectancy for every combination of range length, stop and target. A real effect shows up as a broad coloured *region*, not one bright cell. Compare the net map with the frictionless map to see what costs take.
* The **selection rule** was written down and committed *before* the sweep ran (`docs/DECISIONS.md`, D4): it averages each cell with its neighbours and sits out if no region is positive after costs. "Sitting out" is a legitimate result.
* The **walk-forward** test picks parameters using only past years, then trades the next year. Compare like with like: the report shows the rule next to the primary spec over the same years.
* Many numbers here are *not independent* (all 96 cells share the same days), so no naive p-values are shown. Stage 7 corrects for the number of variants tried.

## How to read the robustness report

* **Bootstrap intervals** show how much a number would move if history had unfolded slightly differently. If the interval for expectancy excludes zero, the result is not luck (here, in the negative direction).
* **Monte Carlo of trade order** asks whether the drawdown is just bad sequencing of the same trades.
* The **forest plot** shows every slice with its interval; a slice whose bar crosses the vertical zero line says nothing either way.
* **Deflated Sharpe and the Reality Check** answer: "if I had tried this many variants of a strategy with no edge, how good would the best look by luck alone?" A result only counts as distinguishable from noise if it beats that bar.
* Two ideas formed *after* seeing earlier results (volatility regime, and "is it just drift?") were pre-registered and tested here; they did not hold up on development data and will each get exactly one holdout test.

## How to read the final report

* **Section 1** is the answer in plain English, produced mechanically from rules committed *before* the holdout was opened (`docs/DECISIONS.md`, D8). "No edge established" means at least one of three pre-declared conditions failed; the report says which.
* **Section 3** puts the development period and the untouched holdout side by side, every statistic with a 95% interval. **Section 4** lists the three tests declared in advance and their outcome, including failures.
* **Section 9** discloses the one wording correction made after the holdout result was known. The verdict and all numbers were unchanged by it.
* The holdout is **spent**: it has been read once for the decision (plus once to verify reproducibility; both logged in `logs/holdout_access.log`). Do not use it to tune anything.

## Repository layout

```
config/config.yaml     every parameter
src/orb/data/          loading, validation, contracts, calendar, daily classification, holdout guard
src/orb/signals/       the opening-range breakout signal engine (vectorised + event-driven reference)
src/orb/execution/     fills, stops, targets, commission and slippage (vectorised + bar-by-bar twin)
src/orb/analysis/      metrics and the trials registry (grows in Stages 5-7)
logs/trials.jsonl      every strategy variant evaluated (for the multiple-testing correction)
src/orb/reports/       report and chart generation
tests/                 unit tests
logs/holdout_access.log  audit trail of every access to the 2024+ holdout
docs/                  plan and published stage reports
```

## Data licence

The market data is licensed by Databento and is not redistributed here. `data/` is git-ignored.
