# NQ 15-minute Opening Range Breakout: research plan

Status: **Stage 1 (plan), awaiting approval.** Findings below come from inspecting the raw Databento file
(`GLBX-20261003-JGLTQ9L4MH`, SHA-256 verified against the manifest).

## Stage 0: data inspection findings

| Question | Finding |
|---|---|
| Source | Databento `GLBX.MDP3`, `ohlcv-1m`, 2010-06-06 to 2026-10-01 |
| Period | Starts June 2010, so 2010 is partial (145 tradable days). 2026 ends Oct 1 (188 days). |
| Timezone | UTC, labelled at bar open. The 09:30 ET bar averages 3,892 contracts vs ~230-400 before it. DST verified: 09:30/09:29 volume jump holds on 91.3% of days in US/EU DST-mismatch windows vs 91.5% overall. |
| Continuous vs contracts | Individual contracts (parent symbology `NQ.FUT`). 40 quarterly outrights, raw prices, not back-adjusted. |
| Extra rows | 546,935 NQ calendar-spread rows and 7.2M MGC rows. Both dropped. |
| Symbol collision | `NQH0` is both Mar 2010 and Mar 2020. Key on `instrument_id`, not symbol. |
| Session | Full ~23h Globex session, 17:00-18:00 ET maintenance break. Only 09:30-16:00 ET is used. |
| Rolls | 66 clean H/M/U/Z volume rolls ~8 days before expiry. Front contract has median 99.9% of RTH volume but only 50-80% on roll days. 142 days (3.4%) have leader share < 0.8. |
| Early closes | 127 days end before 14:00 ET: cash-holiday short sessions plus true half-days. |
| Incomplete ranges | 2020-03-09 (5 range bars), 2020-03-12 (7), 2020-03-16 (1), plus two days with 14. 2012-07-03 is missing ~160 window bars. |
| Integrity | No duplicates, no OHLC violations, no off-tick prices. Bars with range > 1% are real events. A missing minute means no trades (Databento emits no empty bars). |

Tradable universe: of 4,194 RTH dates, 4,058 are non-early-close days with all 15 range bars and all 370 window bars.

Not yet verified: the `ts_event` = bar-open convention is inferred from the volume spike, and the data has not yet
been cross-checked against an exchange calendar.

## Stage 1: proposed design

**Layout:** `config/config.yaml` (single source of parameters), `src/orb/{data,signals,execution,analysis,reports}/`,
`tests/`, `scripts/`, `README.md`. One command: `python -m orb run`. Fixed seeds, pinned dependencies.

**Defaults (open for change):**
1. Contract per day = highest RTH volume on the *previous* day (same-day volume would be lookahead). Roll days
   (leader change, or leader share < 0.8) are flagged and excluded from primary results, with an "include rolls" variant.
2. Day filter: exclude early closes, cash holidays, days with < 15 range bars, and days with window gaps. Each
   exclusion is logged with its reason.
3. Range = bars labelled 09:30-09:44. First signal bar is the 09:45 bar (closes 09:46:00); entry at the next bar's open.
4. R = distance from actual entry open to the opposite range side (not the range width).
5. Fills: entries and stops are market-style with slippage; a bar opening beyond the stop fills at the open; targets
   are limits with no slippage, filled on touch (a trade-through-by-1-tick variant is included); time exit at the
   open of the 15:55 bar; stop wins if stop and target share a bar, and the count of such trades is reported.
6. Costs: $2.00/side NQ, $0.50/side MNQ (configurable), slippage 0-3 ticks, results at 0x/1x/2x cost.
7. Holdout 2024-01-01 onward, guarded in code (refuses to load without an explicit flag, every access logged).
   Walk-forward on 2010-2023 with rolling 5-year train / 1-year test.
8. Every variant evaluated goes into a trial registry feeding the deflated Sharpe and a reality check.
9. Regimes: 2010-2019, 2020, 2021, 2022, 2023+. Regime tables use dev data only until the holdout is run.

## Stage plan

2. Data: loader, validation, data-quality report. 3. Signal engine plus no-lookahead tests. 4. Execution model.
5. Backtest and trade log. 6. Validation (IS/OOS, walk-forward, heatmaps). 7. Robustness. 8. Final report.
Each stage is committed and pushed, then paused for approval.

## Changes made during Stage 2 (after plan approval)

* **Vendor quality ledger.** The Databento download includes `condition.json` marking 32 dates `degraded`. This was not
  in the original plan. It explains 5 of the 6 NYSE sessions that have no data at all in 2014, so days it flags are now
  excluded by default (`filters.exclude_vendor_degraded`).
* **Calendar check.** NYSE calendar (`exchange_calendars`) agrees exactly with the bars about short sessions (128/128).
* **Contract count.** 71 contracts (not 40): the 40 symbols collide across decades, so everything is keyed on `instrument_id`.
* **Known limitation.** Abrupt-flip roll days (volume flips to the next contract on the same day, after the old one held
  >= 80% the day before) cannot be detected in advance and are not excluded. Stage 6 reruns with rolls in/out.

## Changes made during Stage 3

* **New parameter `signal.last_entry_bar` (default 15:54).** The spec only says "flat by 15:55". A signal needs an entry bar before
  the exit bar, so 15:54 is the latest permitted entry bar; later signals are not traded. Exposed for sensitivity testing.
* **Finding:** with a 15-minute range, 99.8% of development days produce a signal and 70% fire before 10:00 ET
  (3,229 development signals). This is effectively a trade-every-day strategy; the 15-minute range is narrow relative to the day.
* **Known residual lookahead:** the day universe excludes 12 development days for a missing minute later in the session. This is not
  knowable at 09:46 and slightly favours quieter days. Tiny, but disclosed.

## Changes made during Stage 4

* **Spread is folded into slippage.** With no quote data, `slippage_ticks` is the all-in adverse cost per market-type fill
  (entry, stop, time exit); target limits assume none. 1x = 1 tick and $2.00/side NQ ($0.50 MNQ); cost scale multiplies both.
* **Levels from the actual fill.** R and the target are computed from the slipped entry fill, as a bracket order placed after the fill would be.
* **Trials registry started** (`logs/trials.jsonl`, committed). The primary spec is trial #1. Cost scales and fill-ambiguity bounds are
  scenarios on one variant, not new trials; any change to range length, stop, or target will be a new trial.
* **Observation (development data, primary spec, no tuning):** negative expectancy even with zero costs (about -0.02R, naive 95% CI
  includes 0) and about -0.05R at 1x costs. Not interpreted further until Stages 5-7.

## Changes made during Stage 5

* **Regimes added to the config** (`regimes:`): 2010s low-vol, 2020 COVID, 2021 post-COVID (not in the brief; added so every date has
  exactly one regime), 2022 bear market, 2023 onward. A test enforces that regimes neither overlap nor leave gaps.
* **`run_backtest(cfg, partition, orb_overrides=..., **exec_overrides)`** is the single entry point later stages reuse.
* **Trade log is not committed** (it contains licensed market prices); it is rebuilt by `python -m orb backtest`.
* **Post-hoc hypothesis logged (D3):** by-year results hint at better performance in higher-volatility years. To be tested in Stage 7
  with a pre-declared definition, as a counted trial, and confirmed on the holdout or reported as failed.

## Changes made during Stage 6

* **Protocol pre-registered** (DECISIONS D4, commit 57bc384) before the sweep: 96-cell grid, smoothed selection with a "sit out" option, IS/OOS split,
  rolling walk-forward, and mechanical promotion criteria.
* **Outcome (development data):** 5 of 96 cells positive net, 51 of 96 positive frictionless. The rule sat out on the full development period, on the
  in-sample window and in 7 of 9 walk-forward folds. No variant is promoted; the primary spec is the only holdout candidate.
* **Weakness noted, not changed:** criterion (b) passed on only 2 active folds (D5). Strength of evidence is for Stage 7 to judge.
* **Second post-hoc hypothesis logged (D5):** the gross-positive "no target + tight stop" cluster may be market drift; to be tested by long/short split and a benchmark.

## Changes made during Stage 7

* **Protocol pre-registered** (DECISIONS D6, commit 7ad355d) before any Stage 7 result: stationary bootstrap, trade-order Monte Carlo, a frozen volatility-tercile definition,
  a direction-label permutation test, deflated Sharpe and White's Reality Check, interpretation thresholds, and the holdout plan.
* **Outcome (development data):** primary spec interval excludes zero on the negative side; D3 and D5 not supported; no cell distinguishable from noise (DSR 0.013, RC p = 0.679). See D7.
* **New committed artefact:** `logs/frozen_vol_thresholds.json`, the volatility cut points computed from development data only and reused unchanged on the holdout.
* **Trials registry:** 98 entries (96 grid cells + 2 hypotheses).
