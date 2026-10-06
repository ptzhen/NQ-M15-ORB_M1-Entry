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
