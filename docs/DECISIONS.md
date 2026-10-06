# Decision log

Decisions made by the project owner during the research, recorded so they cannot silently drift.
Newest last. Each entry says what was decided, why, and what it means in practice.

## D1 (2026-10-06): proceed through every stage even if the results look bad

**Decision.** If development results are poor (negative or zero expectancy, no stable parameter region),
the project **continues through all eight stages** and ends with an honest report. A negative result is a
valid finding: the brief is to find out whether a real edge exists, not to produce a good-looking curve.

**Why.** Stopping, or quietly re-designing the strategy until it works, is how false edges get published.
Knowing that "the textbook 15-minute NQ ORB has no edge after costs" is itself the useful answer.

**Guardrails that go with it** (these are what make "proceed anyway" safe):

1. **No rescue tuning.** The primary specification is the brief's: 15-minute range, stop at the opposite side
   of the range, 1R target. It was registered as trial #1 (`logs/trials.jsonl`) before any sensitivity work.
   A poor result is not a reason to change it, only to report it.
2. **Sensitivity is for understanding, not for finding a winner.** Stage 6 maps range length / stop / target
   to see whether *any broad, stable region* exists. Every variant is logged as a trial, and the multiple-testing
   correction (deflated Sharpe / reality check, Stage 7) is applied to whatever is reported. A single good cell in a
   noisy grid is not evidence.
3. **A variant may be promoted to the holdout only if** it is chosen by a rule written down before the walk-forward
   runs (a broad stable region, not the maximum), and it is still positive after costs out-of-sample on the
   development period.
4. **The holdout (2024-01-01 onward) is run once, at the end, on the pre-declared candidate(s).** If no variant
   qualifies, the primary specification is the only thing run on the holdout. It is run even if development looked
   bad: it is the only unbiased check, and a holdout that is skipped when dev looks bad but run when dev looks good
   is a biased procedure.
5. **The final report states the conclusion in plain English**, including "no distinguishable edge" if that is what
   the evidence says, and does not soften it.

## D2 (2026-10-06): approvals and defaults confirmed by the owner

* Stages 0-4 approved. Stage 1 plan approved with the defaults in `docs/PLAN.md`.
* Cost defaults accepted for now: $2.00 per side NQ, $0.50 per side MNQ, slippage 1 tick all-in per market fill at 1x.
  To be replaced with the owner's broker numbers if provided (`config/config.yaml`, `execution.costs`).
* Vendor-degraded days excluded; roll days excluded by default with an in/out comparison in Stage 6.
* After each stage: commit with a clear message and push to https://github.com/ptzhen/trading-research-work.
* After each stage: summarise what was done, assumptions, and what could invalidate the result; wait for approval.

## D3 (2026-10-06): post-hoc observations are hypotheses, logged when they are made

**Observation (Stage 5, development data, primary spec):** by-year expectancy is clearly negative in 2015-2017 and
around zero / slightly positive in 2019 and 2021-2023.

**Status:** hypothesis only, formed after seeing the results. Candidate explanation: the strategy does relatively better in
higher-volatility regimes. **Not** evidence of an edge.

**How it will be tested:** (1) define "volatility regime" in advance from information known before the open (e.g. prior-day
range or trailing realised volatility, thresholds set by terciles on the development period, not tuned to the P&L), (2) count it as
a trial in `logs/trials.jsonl`, (3) apply the multiple-testing correction, (4) check it once on the holdout. If it is not
confirmed out of sample it is reported as a failed hypothesis.

## D4 (2026-10-06): Stage 6 protocol, fixed BEFORE any sweep is run

Written and committed before the parameter sweep, walk-forward or in/out-of-sample results exist. Anything below that is later
changed must be recorded as a new decision with the reason, and the original stays visible in git history.

**Search space (every cell is one trial, 96 in total; costs at 1x; target fill = touch; ambiguity = stop-first):**

* range length (minutes): 5, 10, 15, 30, 45, 60
* stop (fraction of range width from the breakout side's opposite edge; 1.0 = opposite side, 0.5 = midpoint): 0.5, 0.75, 1.0, 1.5
* target: 1R, 1.5R, 2R, none

The primary specification (15 min, 1.0, 1R) is one of the 96 and keeps its existing trial identity (no double counting).

**Selection rule (the same rule everywhere it is used).** Score each cell by its net expectancy in R on the training window, then
**smooth** that score over the cell and its immediate neighbours in the grid (average over all cells within one grid step in each of the
three dimensions, edges use the neighbours that exist). Pick the cell with the highest smoothed score. This prefers a broad
stable region over an isolated peak. **If the best smoothed score is not above zero, no variant is selected and the strategy sits out**
(zero trades) for that window. The single best raw cell is never used for selection; it is only shown for illustration.

**In-sample / out-of-sample split (within the development period):** train 2010-06 to 2018-12, test 2019-01 to 2023-12.

**Walk-forward:** rolling windows, 5 calendar years of training and the next calendar year as test, test years 2015 to 2023 (nine folds;
the first fold's training window is the 4.6 years from 2010-06). Selection happens inside each training window only. The stitched
out-of-sample trades are the walk-forward result. Comparators: always trading the primary spec, and the in-hindsight best cell
(shown only to quantify how optimistic in-sample selection is).

**Promotion to the holdout.** The primary specification is **always** run on the holdout (D1). A rule-selected variant is *additionally*
run on the holdout only if **all** hold: (a) the rule selects a cell on the full development period (smoothed score above zero at 1x costs),
(b) the stitched walk-forward out-of-sample net expectancy at 1x costs is above zero, and (c) the selected cell's net expectancy on the single
out-of-sample window is above zero. Passing these only earns a place on the holdout. It is **not** a claim of edge: the conclusion rests on
the multiple-testing-corrected statistics of Stage 7 and on the holdout result itself.

**Scenario analyses (not trials, same variant):** roll days included vs excluded; costs at 0x/1x/2x.

**Reporting commitments:** show the full grid (not only the best cells), both net and frictionless, the share of cells with positive
expectancy, and how well in-sample ranking predicts out-of-sample ranking across all 96 cells.

## D5 (2026-10-06): Stage 6 outcome notes and a second post-hoc hypothesis

**Applied as pre-registered (D4), development data only:** the rule sat out on the full development period and on the in-sample window
(no smoothed region had positive net expectancy), and in 7 of 9 walk-forward folds. Criteria (a) and (c) failed, so **no variant is promoted;
the primary specification is the only holdout candidate.**

**Weakness noted, not fixed:** criterion (b) passed mechanically on only two active walk-forward folds (2022-2023) with a confidence interval
including zero. D4 is not amended after seeing results. The strength of evidence is judged by the Stage 7 statistics (bootstrap, deflated Sharpe).

**Second post-hoc hypothesis (to be tested in Stage 7, as for D3):** the frictionless grid shows a cluster of positive cells for *no target + tight stop +
short range*, mostly consumed by costs. Because "no target" means holding to the close, this may be NQ's 2010-2023 upward drift rather than
breakout behaviour. Test by splitting long vs short, and by comparing with a same-exposure, same-hold-time benchmark that ignores the breakout.
Counted against the multiple-testing correction; not a finding.

## D6 (2026-10-06): Stage 7 robustness protocol, fixed BEFORE any Stage 7 result exists

Written and committed before the bootstrap, the volatility-regime test, the direction test or the multiple-testing corrections were run.

**1. Bootstrap (primary specification, 1x costs, development data).** Stationary bootstrap (Politis-Romano) of the date-ordered series, mean block length
10 trading days, 10,000 resamples, seed from the config. It respects clustering by regime, unlike the naive intervals of earlier stages. Statistics:
expectancy (R), win rate, profit factor, annualised Sharpe and Sortino of the daily return on notional (days without a trade = 0; sqrt(252)).
A plain i.i.d. bootstrap is shown beside it only to show how much narrower it is.

**2. Monte Carlo of trade order.** 10,000 random permutations of the trade sequence; distribution of maximum drawdown versus the realised one.
An observed drawdown far beyond the shuffled distribution indicates losses cluster in time (a regime effect).

**3. Volatility regime (tests hypothesis D3).** Measure, known before the open: the mean of (RTH high - RTH low) / RTH close over the 20 trading days **before** the
trade date. Terciles are cut with the 1/3 and 2/3 quantiles of that measure over development-period trade days, then **frozen** (the same numeric thresholds are
used for any later data). The hypothesis, stated before testing: *the primary spec has positive net expectancy in the high-volatility tercile.* Reported with block-bootstrap
intervals for each tercile and for the high-minus-low difference. It is one additional trial.

**4. Is the breakout direction informative? (tests hypothesis D5).** For every primary-spec signal day, take the gross return in basis points of price from the entry
price to the 15:55 open in the signal's direction (hold-to-close, no stop, no target, no costs). Permutation test: randomly reassign the direction labels across signal
days (keeping the number of longs and shorts), 20,000 times; one-sided p-value for the observed mean. This removes market drift (shuffled labels share it) and isolates whether
the breakout direction carries information. Also reported, descriptively: the long/short split of the no-target cluster's best cell and an always-long benchmark with the same entry
times. It is one additional trial.

**5. Multiple-testing corrections** on the 96 grid cells at 1x costs, using daily returns on notional:
* **Deflated Sharpe ratio** (Bailey and Lopez de Prado) for the best-Sharpe cell and for the primary spec, with N = the number of trials in the registry at the time of
  computing, the variance of Sharpe ratios across the 96 cells, and the skewness/kurtosis of each cell's own returns.
* **White's Reality Check**: stationary bootstrap (mean block 10 days, 2,000 resamples) of the maximum over the 96 cells of sqrt(T) x mean daily return, against the null that no cell has positive expected return.

**Interpretation thresholds, fixed now.** A result is called *distinguishable from noise after correction* only if DSR >= 0.95 **and** the Reality Check p-value < 0.05 for the cell
in question. Otherwise it is *not distinguishable*. A hypothesis (D3 or D5) is called *supported on development data* only if its one-sided p-value is < 0.05 (4) or the high-tercile lower
confidence bound is above zero (3); support on development data earns it a holdout test but is not a claim of edge.

**Holdout plan, declared now regardless of how development looks (D1):** the holdout is run once in Stage 8 on (i) the primary specification, (ii) the primary specification restricted to the high-volatility tercile
using the frozen thresholds (hypothesis D3), and (iii) the direction permutation test (hypothesis D5) on the holdout signals. No other variant is promoted (Stage 6 promoted none). The
final conclusion states the outcome of all three, including failures.

## D7 (2026-10-06): Stage 7 outcomes (development data) and confirmation that the holdout plan is unchanged

Applied exactly as pre-registered (D6):

* **Primary spec, 1x costs:** expectancy -0.051 R, 95% block-bootstrap interval -0.083 to -0.019 (excludes zero on the negative side). Frictionless: -0.020 R, interval -0.051 to +0.011.
  Block and i.i.d. intervals have almost the same width (ratio 0.98): little serial dependence trade to trade.
* **D3 (volatility regime): NOT supported on development data.** Low / mid / high prior-volatility terciles: -0.063 / -0.032 / -0.062 R; high-minus-low difference -0.000 R
  (interval -0.081 to +0.082). Thresholds frozen in `logs/frozen_vol_thresholds.json`.
* **D5 (breakout direction vs drift): NOT supported on development data.** Hold-to-close return in the signal direction -0.77 bps vs +0.08 +/- 1.60 under shuffled labels (p = 0.703);
  the market drifted +2.51 bps from entry to the close regardless of direction.
* **Multiple testing:** best of 96 cells has a deflated Sharpe of 0.013 and White's Reality Check p = 0.679: not distinguishable from noise. The primary spec is not distinguishable either (its Sharpe is negative).

**Holdout plan unchanged (D1, D6):** the holdout is run once in Stage 8 on (i) the primary spec, (ii) the primary spec restricted to the high-volatility tercile with the frozen thresholds, and (iii) the
direction permutation test. These are run even though (ii) and (iii) failed on development data, as declared before the results existed. No variant is added or removed on the strength of what
development data showed.

## D8 (2026-10-06): final-run procedure and conclusion rules, fixed BEFORE the holdout is touched

**One run.** The holdout (2024-01-01 to 2026-10-01; 2026 is a partial year) is loaded once by `python -m orb final` with `ORB_UNLOCK_HOLDOUT=yes`. A lock file
(`logs/holdout_final_run.json`) is written on completion; later invocations refuse unless `--reproduce` is passed, which only re-derives the same numbers for verification and
is logged as such. No decision may be taken from a reproduction. The pipeline was dry-run on **development data only** (2021+ treated as a pseudo-holdout) to remove bugs
before the real run.

**What is run on the holdout (declared in D1 and D6, unchanged by development results):**
(i) the primary specification (15-minute range, stop at the opposite side, 1R target, 1x costs, with 0x and 2x cost scenarios on the same trades);
(ii) the primary specification restricted to the high-prior-volatility tercile, using the frozen thresholds in `logs/frozen_vol_thresholds.json` (hypothesis D3);
(iii) the direction-label permutation test on the holdout signals (hypothesis D5). Descriptive cuts (by year, side) come from the same single run.

**Statistics:** stationary block bootstrap, 10,000 resamples, mean block 10 trading days, seed from the config, for every interval.

**Conclusion rules (written now):**
1. Primary spec on the holdout at 1x costs is *negative and distinguishable* if the 95% interval for expectancy lies entirely below 0; *positive and distinguishable* if entirely above 0;
   otherwise *not distinguishable from zero*.
2. The overall conclusion is **"evidence of a real, tradable edge"** only if ALL of: (a) the holdout interval is entirely above 0 at 1x costs; (b) the holdout expectancy is still above 0 at 2x costs;
   (c) the development result did not contradict it, i.e. the development interval at 1x costs does not lie entirely below 0. If any fails the conclusion is **"no edge established"**,
   and the report says in plain English which condition failed. There is no third, softer category.
3. Hypothesis D3 is *supported on the holdout* if the high-tercile interval lies entirely above 0 (>= 20 trades required, else "untestable"). Hypothesis D5 is *supported* if the permutation p-value is below 0.05.
   Three declared tests are run, so a Bonferroni-adjusted level of 0.05/3 is also shown; the rules above are not relaxed or tightened by it.
4. The report states the outcome of all three tests, including every failure, and does not drop or reorder them.
