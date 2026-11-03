# Monthly Reports: First-Morning Close

Status: implemented locally; not deployed, not committed.

Implementation verified 2026-11-03 (local test DBs/Redis only):

- Earlier reported coverage (not retained as a complete evidence set; do not use
  the approximate aggregate as a release gate): monthly_period, measurement_observation_slots(+postgres),
  monthly_manifest(+postgres), monthly_dawn_close (new file),
  monthly_sov_incident_retrying(+postgres), monthly_report_gap_notifications,
  operations_center_report_queries, monthly_report_delivery,
  sov_repeat_alert_cut, sov_monthly_chunk_continuation, sov_monthly_redesign,
  celery_routing, w8_report_guarantee(+postgres), monthly_remasure_gate,
  monthly_sov_postgres, monthly_sov_repository, monthly_sov_comparability,
  monthly_slots, measurement_selection_service, monthly_sov_report,
  monthly_cohort_bootstrap, monthly_template_refresh(+postgres),
  monthly_report_manual, monthly_report_integrity_audit,
  report_artifact_validation, monthly_report_sample_integrity, monthly_events,
  beat_scheduler, redbeat_reconcile, run_beat_entry, milestone_monthly_scope,
  monthly_cohort_cost_threshold, monthly_control/slot/cohort migrations,
  monthly_artifact_reconciliation/recovery/corruption, and the full
  tests/integration monthly suite.
- Admin pure-lib: 60/60 node tests (operations-center, schedule,
  report-review).
- PIN updates: beat crontabs, window/deadline/judgment constants, recovery-copy
  strings, and old-window mock dates updated to the day-1 dawn contract;
  stale shared-DB period-run state cleaned inside
  test_three_day_partial_budget_round_robin_runs_through_report_entrypoint.
- Bug found and fixed during verification: claim_slot_stage's post-lock
  deadline check must not overwrite the caller's `now` lease clock.

Lead review rework (settled subset, applied 2026-11-03):

- `slotted_observation_adequacy` extracted as the single source of the final
  adequacy algorithm in `measurement_manifest_policy`; `summarize_manifest`
  calls the same helper on all planned cells.
- `reopen_incomplete_manifest_for_recovery` refuses to reopen closed terminal
  manifests (COMPLETE/LIMITED/UNAVAILABLE); unresolved incomplete still reopens.
- `is_monthly_recovery_window` is a safe predicate: invalid year/month → False.
- `run_monthly_sov_measurement` passes an eligibility-filtered registration to
  `_bootstrap_unmeasured_hospitals` and `_report_monthly_cohort_gaps` (day-1
  signups get no prior-month bootstrap or cohort-gap).
- Admin run copy: BLOCKED is generic (measurement/PDF/content), directs to the
  operations center; COVERAGE_COMPLETE is a finalized-sample label, not
  "everything arrived". Artifact safe message no longer tells staff to rebuild
  while bounded repair is active.
- Test isolation: `test_three_day_partial_budget_round_robin` runs inside an
  outer transaction (savepoint binding) on dedicated period `2098-11`; no
  pre-existing shared rows are deleted.
- Current-contract docs/comments updated (slack-notification-policy, CLAUDE.md,
  system-map, ops runbooks, autonomy_slo, incident_backlog, incident_types,
  notification_copy, autonomous_recovery, operations docstring).
- `tests/test_monthly_dawn_close.py` regression cases: terminal-manifest
  no-reopen (byte-identical across calls), incomplete still reopens,
  summary/adequacy agreement, invalid-period predicate safety, claim/checkpoint
  lock-wait fencing across 07:00, four calendar cases
  (Apr→May, Feb2027→Mar, leap Feb2028→Mar, Dec→Jan), REPAIR_SPACING=30min,
  finalization gate at 22:15Z, report copy/action pre/post-09:00, registration
  filter bounds bootstrap/gaps to eligible hospitals.

Verification (this rework round, exact):

- Backend: `source /tmp/reputation-testenv.sh && cd backend && .venv/bin/python
  -m pytest <20 affected test files> --junitxml=/tmp/rework-final-junit.xml -q`
  → 343 passed (log: /tmp/rework-final-log.txt).
- Admin: `node --test lib/report-run.test.ts lib/operations-center.test.ts`
  → 58 passed (log: /tmp/rework-admin-log.txt).
- Earlier rounds retained: 78 passed (/tmp/rework-narrow-junit.xml,
  /tmp/rework-narrow-log.txt); savepoint-isolated round-robin 1/1;
  admin report-run 7/7.

Final hardening round (lead-authored sov_engine/exposure/config changes +
deploy.sh day-1 window; tests-only on this side):

- Backend final gate: `pytest <45 affected files>
  --junitxml=/tmp/monthly-dawn-release-backend.xml -q` → 764 passed + 1
  stale PIN (`test_guard_defaults_pin_locked_august_conversion_envelope`
  expected the old daily default 1260; updated to the authored 4260,
  verified passing). Logs: /tmp/monthly-dawn-release-backend.{log,xml}.
- Real PG deadline-crossing tests: late answer settles but never
  checkpoints (no judge call), late judgment settles but writes no
  verdict/record, reserve crossing refunds without provider call —
  tests/integration/test_measurement_observation_slots_postgres.py (6/6).
- Judge separation regressions: own MATCHED/NOT_MATCHED/AMBIGUOUS survive
  competitor parse failure (entries FAILED/None, never false/zero);
  self-parse failure skips competitor call; competitor
  SoftTimeLimitExceeded propagates.
- `_competitor_mention_count` returns None on unknown judgments;
  `_competitor_outcomes` never counts unknown entries (no false 0%).
- Cohort cost threshold: min(daily,monthly)/(15×platforms×min(repeats,20)×3)
  — 4260/4260→9, 3000/9000→6, 9000/9000→20, repeats10→10, one
  platform→18, 100→1.
- Deploy window: day-1 00:15~09:00 KST blocked (03:00/05:00/09:00), day-2
  same times and day-1 09:01 allowed, override records the monthly window,
  month-boundary build crossing stops before mutation — 8 tests pass.
- Env templates aligned: COST_GUARD_DAILY_SOV_QUERIES=4260,
  SOV_MONTHLY_WINDOW_START_DAY removed (setting deleted).
- Admin: node --test 4 libs 67/67, tsc --noEmit clean, npm run build
  succeeds. Guards: db-budget-guard 75/80; copy-guard OK via venv python
  (system python3.9 in Makefile crashed on ast decoding — tool issue, not
  content).
Decision date: 2026-11-03. All times below are Asia/Seoul.

## Normal Completion And Deployment Hardening

- Normal operation must collect the entire frozen plan. Missing answers are an
  execution/provider incident, not an expected property of monthly data.
  LIMITED/UNAVAILABLE remain truthful exception handling, not the completion goal.
  Identity ambiguity is retained as a distinct observed judgment, never invented
  as an API failure or a negative mention.
- A successful self judgment survives a supplementary competitor-analysis
  failure. Failed competitor entries persist `is_mentioned: null` with failure
  evidence; they are not zeroes. Competitor comparisons are withheld when their
  observations are incomplete, and cannot close an existing competitor gap.
- Live read-only preflight on 2026-11-03 found API/Worker/Beat release
  `2baa99f1012a47c147f701dc15aefd72eb703886`, daily SoV limit 3000,
  monthly limit 9000, and 12 ACTIVE hospitals (the cohort can be smaller).
  Captures: `/tmp/monthly-dawn-prod-preflight.json` and
  `/tmp/monthly-dawn-prod-http.json`. These are captured inputs, not live claims.
- With 15 questions, two platforms, five repeats, and at most one answer plus
  self/competitor judgment batches, 12 hospitals require at most 5400 units
  before retries. Proposed production daily limit: 9000; monthly limit remains
  9000. Kill switch, stage attempt bounds, and monthly guard remain enabled.
  This operational limit change still requires deployment approval.
- The code default daily limit matches its existing monthly limit (4260).
  The cohort warning uses the smaller daily/monthly allowance and includes
  judgment stages, rather than understating cost using answer calls alone.
- No historical report regeneration, paid verification measurements, schema
  migration additions, or content publication are authorized by this change.

## Product Contract

Create a report on the promised date from the facts actually obtained. Measurement
availability is report content, not a reason to postpone the report until day 8.
The previous calendar month's report must be ready for staff by 09:00 on day 1.
This includes February, leap years, 30-day months, and December rollover.
Readiness does not mean automatic customer delivery: AE delivery remains manual.

## Authoritative Timing Configuration

These values are the implementation specification, not tunable suggestions:

| Action | Time on day 1 |
| --- | --- |
| First previous-month measurement dispatch | 00:15 |
| Additional unresolved-work dispatches | Every 30 minutes, through 06:45 |
| Measurement deadline (exclusive) | 07:00 |
| Scheduled report finalization/build | 07:15, 07:45, 08:15, 08:45 |
| Report availability judgment | 09:00 |

- Remove the day-24..31 monthly measurement schedule.
- Remove the day-2..8 measurement/report waiting period.
- Keep calendar reporting boundaries and the existing 00:15 earliest close.
- Keep the 15-question, two-configured-platform, five-repeat sampling plan,
  existing provider/model settings, cost guards, and per-stage attempt caps.
- Use `month_start_dawn` as the new frozen `measurement_window` value. Do not
  compare it as the same basis as the old `month_end` or `month_start` values.
- Set `REDBEAT_SCHEDULE_VERSION` to `2026-11-03.1`.
- Exact Beat crontabs: measurement `hour="0-6", minute="15,45",
  day_of_month="1"`; reports `hour="7,8", minute="15,45", day_of_month="1"`;
  gap summary `hour=9, minute=0, day_of_month="1"`.

## Period Helpers

In `monthly_period.py`:

- Retain `MONTHLY_RECOVERY_END_DAY = 1` and `MONTHLY_FINALIZE_DAY = 1` for their
  existing consumers. Add `MONTHLY_MEASUREMENT_END_HOUR = 7` and
  `MONTHLY_REPORT_FINALIZE_HOUR = 7`; retain `MONTHLY_FINAL_JUDGMENT_HOUR = 9`.
- `monthly_recovery_deadline(year, month)` returns
  `reporting_period(year, month).ends_at + timedelta(hours=7)`.
  It is an exclusive boundary, with no last-second adjustment.
- Add `monthly_report_finalization_at(year, month)` returning
  `reporting_period(year, month).ends_at + timedelta(hours=7, minutes=15)`.
- `monthly_final_judgment_at(year, month)` returns
  `reporting_period(year, month).ends_at + timedelta(hours=9)`.
- `is_monthly_recovery_window(now, year, month)` is exactly
  `period.closes_at <= _as_kst(now) < monthly_recovery_deadline(year, month)`.
  Thus only the just-ended month is eligible, only on day 1.
- `_resolve_monthly_measurement_period` resolves missing year/month to the previous
  calendar month and returns it only when that window is open. Reject partial
  dates, current/future months, day 24..31, day 2, and requests at/after 07:00.
- `_monthly_sov_retry_window` delegates to that same window; remove its separate
  current-month/day-24 rule and remove `SOV_MONTHLY_WINDOW_START_DAY`.
- The low-level incident registry computes `next_start + timedelta(hours=9)`;
  it must not retain its independent seven-day offset.

## Measurement Dispatch And Provenance

`run_monthly_sov_measurement` dispatches for the previous reporting month, never
the current calendar month. Intersect enrolled monthly hospitals with historical
service eligibility for the reporting period, so a new day-1 customer does not
receive a previous-month measurement. Keep existing bootstrap behavior for
eligible unmeasured hospitals; use actual current dates for weekly bootstrap
identities and the report period for monthly incident identities.

Monthly dispatch uses the frozen `month_start_dawn` tracking protocol. Generalize
the authoritative-tracking predicate in `monthly_manifest.py` to accept
`month_end` and `month_start_dawn`; preserve its tracking fingerprint/size checks.
Do not supersede an already measured authoritative manifest, a closed manifest,
or a manifest referenced by a report. Do not rewrite existing reports or
historical measurement provenance.

At repeated dispatches, continue only unresolved frozen slots/stages. Reuse
received answers; do not resample non-mentions or ambiguous judgments. Do not
raise provider concurrency, question counts, repeats, attempts, or cost budgets.

## Finality And Missing Observations

For a monthly manifest with a valid repeat contract, define:

1. `planned_cells`: non-EXCLUDED frozen cells.
2. `expected`: `len(planned_cells) * repeat_count`.
3. `rows`: all observation slots belonging to those cells.
4. Reject corrupt repeat sets: repeat numbers outside `1..repeat_count`,
   duplicate repeat numbers within a cell, or more slots than the frozen plan.
5. `missing = expected - len(rows)`; missing slots remain pending, not failed
   answers, invented observations, or non-mentions.
6. `finished = deadline_reached or (missing == 0 and
   all(slot_is_terminal(row) for row in rows))`.
7. Summarize actual rows with `summarize_observation_slots(rows,
   deadline_reached=finished)`. Use `dataclasses.replace` to set
   `planned_slots=expected`, `pending_slots=summary.pending_slots + missing`,
   and status as follows:
   - COMPLETE iff `expected > 0 and confirmed_slots == expected`;
   - LIMITED iff `finished and confirmed_slots > 0`;
   - UNAVAILABLE iff `finished and confirmed_slots == 0`;
   - IN_PROGRESS otherwise.

An empty planned-cell set remains a configuration error, not a fabricated
COMPLETE measurement. Legacy manifests retain their existing cell-level
finalization rule at the new deadline.

The monthly completion helper and report preflight accept final COMPLETE,
LIMITED, or UNAVAILABLE, including early all-terminal outcomes. Missing slot rows
at 07:00 cannot keep an otherwise valid frozen manifest indefinitely pending.
Use the same adequacy in `summarize_manifest`: a closed LIMITED sample is DEGRADED,
a closed UNAVAILABLE sample is BLOCKED but may pass the existing honest
UNAVAILABLE delivery contract. Keep integrity/configuration blockers intact.
No rate formula, scoring cohort, comparison threshold, or null semantics change.

## Deadline Fencing

Monthly work must stop purchasing and accepting observations at 07:00, not just
stop dispatching new tasks:

- Thread an optional `deadline: datetime | None = None` through
  `_execute_paid_observation_slot`, `claim_slot_stage`, `checkpoint_answer`, and
  `checkpoint_judgment`. Existing V0/weekly callers keep `None`.
- Monthly callers supply `monthly_recovery_deadline(period_year, period_month)`.
- Claims and checkpoints return `None` at/after the deadline, before mutating a
  slot or adding a SovRecord. Check both before and after acquiring the slot lock.
- Check the same deadline before each answer/judgment provider stage. A late
  answer must not trigger a new judgment call.
- Settle already-issued cost reservations normally even if a late response is
  discarded. Do not refund paid attempts or turn a discarded response into 0%.
- Monthly task loops stop at the deadline, including the legacy compatibility
  lane. Late queued work performs no provider calls.

## Reports And Recovery

At 07:15, generate reports for historically eligible service periods using closed
facts, even with partial/unavailable measurements. A month with no manifest uses
the existing honest SETUP report. Preserve PDF/artifact/tenant/medical integrity
checks and manual delivery.

Do not let an earlier PARTIAL report operation or its stale incomplete report
prevent finalization forever: after measurement becomes final, route it through
the existing idempotent automatic report recovery, not an unconditional new
version per tick. Already deliverable reports remain unchanged.

Start the report safety net at `monthly_report_finalization_at`, not 09:00 or day
8. Set the existing two-attempt report repair spacing to 30 minutes, so a first
morning PDF fault can recover before 09:00; keep its bounded budget and incident
deduplication. The 09:00 SLO is an availability check, not a first generation tick.
Infrastructure outages can still prevent file readiness; never label a missing
or unvalidated PDF as completed.

## Staff UI And Documentation

- Replace current-policy day-1..7/day-8/month-end-measurement guidance in CLAUDE.md,
  system-map, current runbooks, APIs, and live UI. Preserve historical releases,
  audits, and plans as historical records.
- During the dawn window, explain that the system is measuring and preparing a
  09:00 report; do not ask staff to repeat report generation.
- Remove the manual monthly remeasurement action from the operations report
  queue. Keep its API compatibility but the automatic window owns normal work;
  do not reduce the existing 12-hour manual stall threshold merely to fit dawn.
- Missing reports before 09:00 are preparation, not HIGH operator tasks. Genuine
  artifact/content failures and manual delivery remain separately actionable.
- A BLOCKED report run can mean PDF or content failure, not only missing
  measurements: change its generic label accordingly, preserving existing true
  failure evidence and report-specific review.
- Use actual observation times and the new frozen protocol; do not describe
  first-dawn AI answers as measurements taken during the previous calendar month.

## Required Regression Cases

- Exact timing before/at 00:15, before/at 07:00, finalization at 07:15, SLO at 09:00;
  UTC equivalents, 30-day month, leap February, normal February, and December.
- Beat fires on day 1 at the exact authored times and not day 2..8 or day 24..31.
- Dawn dispatcher sends the prior month only; excludes service starting day 1.
- Full success, partial confirmed+exhausted, all ambiguous, all failures, missing
  slot rows, and no-manifest SETUP all reach the appropriate honest report state.
- Exhausted/ambiguous observations finalize early without extra paid calls.
- Answer received but judgment failed reuses the answer.
- Claim or provider return crosses 07:00: no new paid stage, no late checkpoint,
  correct reservation settlement, V0 unaffected.
- Repeated ticks/recovery do not duplicate paid observations or ready reports;
  earlier PARTIAL operations cannot block morning finalization.
- New window is non-comparable with old monthly windows; unchanged formula and
  null handling continue to pass existing report integrity/delivery tests.
- Safety net starts at 07:15 and can retry a PDF repair at +30 minutes; SLO remains
  quiet before 09:00 and reports actual missing/unready artifacts afterward.
- Admin/API copy has no active seven-day promises or normal manual-repeat action.

No production queries, deployments, real provider calls, or historical report
rebuilds are authorized by this implementation task.
