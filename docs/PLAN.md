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
