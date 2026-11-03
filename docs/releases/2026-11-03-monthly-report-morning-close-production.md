# Monthly Reports: First-Morning Close - Production

Status: deployed and verified on 2026-11-03 at 15:55 KST.
Runtime code: `76e09e555f13178ef185fb6274b85f9ddba57be5`, pushed to main.
This documentation update does not constitute a second runtime deployment.

## Deployment

Project `mso-platform-481505`, region `asia-northeast3`.
All services below route 100% to their latest ready revision.

| Service | Revision | Image Tag |
| --- | --- | --- |
| API | `reputation-api-00274-v8l` | `reputation:20261103-153556` |
| Worker | `reputation-worker-00264-r86` | `reputation:20261103-153556` |
| Beat | `reputation-beat-00259-9sl` | `reputation:20261103-153556` |
| Admin | `reputation-admin-00115-rwt` | `admin:20261103-155039` |
| Site | `reputation-site-00149-6ck` | Unchanged |

Backend digest:
`sha256:b87b478a13455eecd02b22f663c51d2a080b81b9665469affbf85f17e8aac7dc`.
Admin digest:
`sha256:b18bde5b8c31c851c6eaec44ae7dc304b482963f85305a2be0cebc022cf5c7dd`.
Cloud Build IDs: backend `577d9a15-872d-45b3-9ca4-c6fde34544ed`;
Admin `43739c09-bc84-453b-87c0-dda68d4ef1ed`.

The reviewed commit was exported with `git archive` into
`/private/tmp/reputation-monthly-dawn-release-20261103`.
The build adapter checked the source marker, allowed only backend/Admin contexts
and exact build arguments, and compared registry digests with successful build
results. Untracked user files were not part of the archive or commits.

## Effective Contract

- All times are Asia/Seoul. Measure the previous calendar month on day 1,
  every 30 minutes from 00:15 through 06:45.
- Measurement deadline is 07:00, exclusive. Paid stages and late checkpoints
  are fenced before and after row locks. Received answers survive judgment
  failures and are not purchased again merely to retry judgment.
- Report attempts run at 07:15, 07:45, 08:15, and 08:45. The 09:00 summary
  judges remaining gaps. Artifact recovery stays separate from measurement.
- Normal operation must collect the frozen plan. Limited/unavailable metrics
  are truthful exception handling, not a normal target or fabricated zeroes.
- A successful self verdict survives supplementary competitor-analysis errors.
  Unknown competitor judgments remain null, are excluded from rates, and cannot
  resolve an existing competitor-visibility gap.
- Closed terminal manifests stay closed. Readiness still requires the existing
  delivery gate and a valid doctor-facing artifact. Customer delivery is manual.
- The new `month_start_dawn` protocol is not comparable with old monthly windows.
  Models, prompts, scoring formulas, and the monthly budget were not changed.

## Approved Configuration

- Daily SoV allowance: 3000 -> 9000. Monthly allowance remains 9000.
- The cost-guard kill switch was confirmed inactive in the 16:03 KST follow-up;
  kill-switch support and spending guards remain available.
- Retired `SOV_MONTHLY_WINDOW_START_DAY` removed from the live backend env.
- Code/example daily default: 4260, matching the existing monthly default.
  Production explicitly overrides both to 9000.
- Cohort capacity warnings include answer/self/competitor stages and use the
  smaller daily/monthly allowance. They do not silently exclude hospitals.
- No new migration files. Read-only schema preflight and post-deploy readiness
  both confirmed `0093_add_hospital_search_verification`.

The first backend attempt stopped before build/service replacement because
four unused Terraform keys were absent from the live env snapshot. Empty
placeholders were declared locally and omitted by rendering; the effective env
diff remained exactly the two approved keys. The first Admin attempt also
stopped before build because `ADMIN_DOMAIN` needed explicit invocation input.
The retry supplied the existing production domain; no domain was changed.

## Verification

- The 45-file backend gate had 764 passes and one obsolete daily-default
  assertion. That assertion was updated from 1260 to 4260; the clean 82-case
  follow-up includes the corrected assertion and judgment/comparison regressions.
  The six observation-slot Postgres cases were also rerun after strengthening
  reservation-refund assertions.
- Admin: 67 tests passed, TypeScript check clean, local production build passed.
  Both production Docker builds completed successfully. Copy and DB-connection
  budget guards passed in the documented Python 3.11 environment.
- Production readiness: all 18 checks passed. Current-release benign canaries
  returned from certificates, content, control, default, leadgen, reports, and sov.
- Redis schedule readback: version `2026-11-03.1`, clean, Asia/Seoul; actual
  persisted measurement/report/summary cron fields match the contract above.
- API health reports the exact deployed code SHA; effective daily/monthly SoV
  limits are 9000/9000. Admin login returns HTTP 200.
- All 12 public hospital health endpoints match their authoritative hospital ID,
  slug, canonical host, and unchanged Site revision.
- At the 15:55 KST verification, the newly deployed revisions had zero ERROR
  entries in the recorded Cloud Logging query. This is a point-in-time check.

Paid AI measurements were not executed for verification. Passing schedules,
canaries, and health checks is not proof that the next actual first-day
measurement has completed or that provider billing/latency has been exercised.
No historical report was forcibly rebuilt. Outstanding reports may finalize
automatically from persisted facts under the approved cutover.

## Evidence And Rollback

Local evidence is retained under `.omo/evidence/monthly-dawn-20261103/`:
build results/digests, deployment logs, JUnit logs, schema proof,
`production-readiness-proof.json`, `beat-schedule-proof.json`, and
`post-deployment-proof.json`, and `kill-switch-proof.json`. Configuration proposals contain no plaintext
secrets; credentials were not copied into the conversation.

Pre-deployment revisions are in `rollback-backend.json` and
`rollback-admin.json` in that directory. Backend automatic rollback was disabled
for this rollout: old Worker/Beat code can reactivate the 1-7 day recovery policy
after new-policy finalization. Assess a roll-forward or protected rollback first.
Traffic rollback alone does not undo persisted reports, facts, or Redis schedules.
No database reversal, counter reset, report deletion, or history rewrite was done.
