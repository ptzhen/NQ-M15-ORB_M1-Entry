# NQ 15-minute Opening Range Breakout: does it have a real edge?

This project tests one simple trading idea as rigorously as possible, with the explicit goal of finding out
whether there is a genuine edge, **not** of producing a nice-looking equity curve. A good-looking result
is treated as suspicious until it survives the tests below.

> **Status: Stage 2 of 8 (data) is complete. There are no strategy results yet.**
> See [`docs/PLAN.md`](docs/PLAN.md) for the full plan and [`docs/stage2_data_quality/`](docs/stage2_data_quality/data_quality_report.md) for the current report.

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
python -m orb run       # every implemented stage in order (currently the same as `data`)
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

## Repository layout

```
config/config.yaml     every parameter
src/orb/data/          loading, validation, contracts, calendar, daily classification, holdout guard
src/orb/signals/       (Stage 3)  execution/ (Stage 4)  analysis/ (Stages 5-7)
src/orb/reports/       report and chart generation
tests/                 unit tests
logs/holdout_access.log  audit trail of every access to the 2024+ holdout
docs/                  plan and published stage reports
```

## Data licence

The market data is licensed by Databento and is not redistributed here. `data/` is git-ignored.
