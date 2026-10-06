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
