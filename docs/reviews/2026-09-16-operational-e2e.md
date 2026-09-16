# Operational E2E completion — verified release candidate

Baseline: 6b0c86f67847c0a1b494e8a66fb902f46ed1ce0e.
Final tested runtime source: 62b58bd50e6defbcb73558e8488c72f84229a9dd.
Status: VERIFIED_RELEASE_CANDIDATE (isolated synthetic scope, no production deployment).
Scope: synthetic tenants only; internal Docker network, no production credentials,
production DB, live Slack, cloud writes, deployment or Git push.

## Checkpoint 1 — committed generation outcome idempotency

The first Docker rehearsal exposed duplicate child outcome insertion after a
post-commit incident operation failed. The event-loop error that triggered that
path was a harness error: SERVICE=worker had been omitted, choosing the API pool
instead of the production worker NullPool. That harness configuration was fixed.
The child outcome insertion itself had an independently reproducible defect.

Two new real-PostgreSQL tests failed before the change: repeated and concurrent
writers raised uq_operation_runs_idempotency_scope violations. A savepoint now
contains the duplicate insert; only that specific constraint permits returning
the exact previously committed child. Its state/error/result are not overwritten.
Distinct attempts remain distinct. Unrelated integrity errors still propagate.

Final selected regression: 24 passed, including all 21 redelivery PG cases and
three new child-outcome cases. Full E2E and release verdict are not complete yet.
Raw logs: /private/tmp/reputation-approval-final-20260916-093831.

## Checkpoint 2 — first-pass monthly completion

Real broker/SDK rehearsal persisted 150 confirmed monthly observations, but the
RUN_SOV outcome was PARTIAL. ensure_monthly_slots assigned only the FK, leaving
the preloaded parent collection empty in the non-expiring worker Session.
The persisted slot-set gate correctly rejected that stale in-memory snapshot.
A migrated PostgreSQL regression reproduced false completion before the fix.
New slots now attach through the mapped monthly_cell relationship, preserving
the FK and keeping the already-loaded collection coherent. No readiness gate
was bypassed. The regression and 78 adjacent tests pass (79 total).
Raw before/after logs and JUnit: monthly-finalize-before.log,
monthly-finalize-after.log, monthly-finalize-after.xml in the evidence root.

## Checkpoint 3 — fresh end-to-end completion

A new round started from an empty UTF-8 PostgreSQL 16 database, migrated by the
actual Linux/amd64 backend image through 0079_topic_swap_fallback. Only two synthetic
onboarded hospitals, blank slots and query definitions were seeded. Generated body,
image certification, observation results, reports and delivery history were NOT seeded.
The previous database, wire responses and evidence were archived before reset.

Real Redis/RedBeat ticks dispatched the registered production task names, options
and signatures. The test-only calendar advanced generation, publication, baseline,
month-end measurement and next-month reporting dates; ticks were accelerated.
The following outcomes were asserted from the same database and served surfaces:

- Positive hospital: 12 generated and published articles; negative hospital without
  approved evidence: one DRAFT, no generated body and no accidental publication.
- 12 durable site-revalidation intents and 12 IndexNow intents completed. Three
  batched HTTP IndexNow submissions were accepted by the isolated capture endpoint.
- Baseline and monthly measurement: 300 observations total, 150 per platform;
  monthly 15 questions x 2 platforms x 5 repeats = 150 CONFIRMED durable slots.
- Monthly report: 30/30 question-platform cells, COMPLETE; actual separate internal
  and director PDF files; director PDF binary validation and SHA-256 matched.
- Normal daily summary and terminal exception each reached the local TLS sink once
  successfully, after injected HTTP 503 and actual transport retry (two attempts).
- No per-article publication notification was reintroduced.

## Checkpoint 4 — replay, outage recovery and report-delivery UI

Completed generation, publication, measurement, report and heartbeat schedules
were replayed. Additional provider requests: ZERO. Content IDs, first publication,
body/image hashes, revisions, measurement slot identities and report artifacts
remained unchanged. This is not a guarantee of exactly-once external billing in
all crash windows; it is the observed completed-work replay contract.

The owned Site container was stopped. Two genuine revalidation intents failed
into automatic retry without an early operator notification. Only the negative
tenant's backoff timestamp was advanced until its real retry budget exhausted.
One terminal incident/outbox row resulted. The Site was restarted and the positive
intent recovered; published content and its first-publication facts stayed intact.
No run outcome, attempt budget, readiness or artifact metadata was forced to PASS.

The real production Admin image -> BFF -> API -> migrated DB passed 12 browser
checks: secure/httpOnly session; worker-built report readiness; CSRF and wrong-hash
rejection; actual PDF download and byte hash; delivery; correction; rescission;
re-delivery; reload persistence; cross-hospital 404; desktop/mobile layout; zero
browser exceptions and server 5xx. Four append-only delivery events were retained.
The two-page Korean director PDF was rendered with Poppler and both pages visually
inspected, along with the mobile delivery dialog.
Public checks passed 8 groups: exact tenant health, all 12 articles/canonical URLs
and persisted image bytes, home/content index/sitemap/robots/llms, and tenant isolation.

## Checkpoint 5 — deploy-image execution and regression

Backend, Admin and Site Dockerfiles built and ran as linux/amd64, non-root.
E2E Backend source is 62b58bd; all 394 deployed source/asset/migration files compared
byte-for-byte with the worktree, with zero differences. Admin/Site source did not
change after their 6b0c86f build baseline. Exact image IDs and source digest are in
image-source-proof.json. Site's test build arguments deliberately name the isolated
API/public domain; these are not images configured for real customer traffic.

A separate empty smoke database used the backend image's DEFAULT entrypoint for
SERVICE=migrate, api, worker and beat, without source mounts or runtime adapters.
Migration, API liveness, Worker/Beat dependency readiness and seven signed queue
canaries through the real prefork Worker all passed. APP_ENV remained test with
synthetic credentials and an internal network; production credentials were not used.

Final full Backend regression: 4,103 passed, 0 failures/errors/skips, two warnings.
Admin: 627 tests; Site: 332 tests; both lint/typecheck passed. Ruff, Python/Node
syntax checks, operator-copy guard, database connection-budget guard and diff
whitespace checks passed. The two warnings concern legacy Alembic path splitting
and metadata sorting for existing FK cycles, not a failed migration or skipped test.

Gcloud read-only verification succeeded for all five Ready services, three job
DEFINITIONS (not executions), API IAM policy and aligned secret REFERENCES.
Secret values were not read. Current API invoker policy still contains allUsers;
this pre-existing configuration was recorded, not rewritten or described as a new
private-IAM deployment. No cloud mutation, live Slack or production DB access occurred.

## Final decision — VERIFIED_RELEASE_CANDIDATE

All three previously outstanding local gates are closed: operational E2E,
normal director-report delivery UI, and Linux deployment-image build/execution.
There is no outstanding failing gate in this specified rehearsal. The candidate
can be presented for deployment approval; this verdict is not an executed rollout.
It supersedes the unfinished local gates in 2026-09-16-release-approval.md without
erasing that historical HOLD record.

Explicit boundaries: starts after synthetic onboarding; AI outputs, image-provider
responses and Slack are test endpoints; GCS is a local storage adapter; business
calendar/tick intervals are accelerated. Actual public AI answers, consumer-app
visibility, real GCS/IAM behavior and production-scale load were not tested here.
Normal operational E2E uses solo; separate stock-image smoke proves prefork boot
and queue execution but is not a load/availability test. All 100% mention figures
in the sample report are synthetic fixtures, not customer outcomes.

Before an authorized real rollout: use the deployment runbook's live migration
head check, source/image digest pin, secret/IAM transition checks, rollback capture,
then staged deployment and current-revision public health/canary verification.
Retain legacy grants until old revisions stop serving. These are rollout controls,
not secretly executed steps or claims of production migration verification.

Versioned evidence and SHA-256 manifest:
`docs/reviews/evidence/2026-09-16-operational-e2e/`
`docs/reviews/2026-09-16-operational-e2e-validation.json`
Raw local evidence: `/private/tmp/reputation-approval-final-20260916-093831/container`.

## Preservation and cleanup

The original Reputation worktree remained at d96bd2143ba509cd1fcae73f028286b6d938ae8e.
HEAD, status, binary diff and all recorded pending-file hashes matched the start.
Eight owned internal Docker containers were stopped; their DB/evidence state was
retained. Stock-smoke containers removed only themselves. Two inherited temporary
host servers, two owned disposable PostgreSQL servers and their exact test Redis
were stopped. Eight owned high ports were verified closed. Other project/test
containers were not stopped. Raw final PostgreSQL dump and execution logs remain
local; no test key, credential file, live token or font file is versioned.
Final backend line coverage: 84.74%. The versioned manifest binds every selected
log, JSON, JUnit result, PDF and screenshot to its exact SHA-256.

Lint reproduction note: use the repository's canonical `ruff check backend`.
A combined root-level `--config backend/pyproject.toml` invocation changed the
inferred source root and produced 305 import-order findings on unchanged files.
`--show-settings` confirmed the root mismatch. Canonical Backend and separately
scoped harness commands both passed without changing source or lint rules.

Committed text-log copies normalize terminal line endings and trailing whitespace
only; the JSON records each affected raw SHA-256. Original raw logs remain local.
Binary PDF/screenshots and structured result/JUnit files are preserved byte-for-byte.
