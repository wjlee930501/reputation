# GEO release-candidate verification — 2026-09-16

Status: HOLD. Executed local gates passed; remaining release gates are listed below. This record does not authorize production deployment.
Baseline: cfc14d034452d60dce862fe6b661cc842b31740e.
Worktree: Reputation-geo-hardening; branch codex/geo-autonomy-hardening-20260915.
The inherited uncommitted work was inspected and backed up before changes.
The original Reputation worktree is not edited.

## Checkpoint 1 — migrated database and complete backend suite

A fresh PostgreSQL 16.15 cluster was initialized on 127.0.0.1:55584.
Four dedicated databases were upgraded from empty through Alembic head
0079_topic_swap_fallback. No ORM create_all bootstrap was used by the runner.
A separate fifth database is reserved for the populated 0064-to-head upgrade test.
An isolated Redis container listens only on 127.0.0.1:56584.
The runner clears inherited environment variables, disables dotenv, uses fake
credentials, blocks non-test Python sockets, and restricts native psycopg2 DSNs.

Full backend result: 4,099 passed, zero failures/errors/skips, two warnings.
Coverage: approximately 85%; exact report is retained with the evidence.
Ruff, operator-copy guard, DB connection-budget guard and git diff --check pass.
Validated inherited fixes: bounded logout rate lane; no public-surface intent for
non-public draft edits; fleet-heartbeat readiness registration; distinct feedback
fixtures that actually exercise the active-cap concurrency test after deduplication.

Evidence root: /tmp/reputation-approval-20260916-tqihijy1.
Raw evidence: migrations.log, backend-full.log, backend-full.xml, coverage.xml.
No production DB, customer data, real Slack delivery, deployment or push was used.

## Checkpoint 2 — real Admin browser integration

Production standalone Admin -> real BFF -> isolated API -> migrated PostgreSQL:
29 browser checks passed, zero failures, browser exceptions or HTTP 5xx.
Nine routes were exercised at 1440px and 390px viewport widths.
Login, signed/CSRF-protected mutations, director-feedback deduplication and tenant
isolation, explicit retirement, preserved schedule/content identities and logout
were verified. Four additional stateful checks passed: pause/public 404,
resume/public 200, blocked-report delivery prevention, and no browser exception.
The report-state wrapper preserves readable status/problem text on mobile.
Mobile reports and the blocked-report dialog were also visually inspected.

Admin: 627 unit tests, lint, typecheck and default production build passed.
Site: 332 unit tests, lint, typecheck and default production build passed.
Frontend builds used scrubbed fake configuration and a Node socket egress guard;
running Admin and API also had fixed-loopback-port OS network restrictions.
The test-only logical API host was mapped to loopback inside the harness; no
production URL guard was weakened and no system hosts file was changed.

Real isolated Redis/Celery: seven signed queue canaries passed; two requests for
the daily fleet heartbeat left exactly one PENDING outbox record. No Slack was
sent. This is a broker/enqueue rehearsal, not a full provider/Beat pipeline E2E.
Evidence: admin-browser-final.log, ui/results.json, ui/actions-results.json,
admin-production-build.log, site-production-build.log, approval-broker.json.

## Final decision — HOLD, not unconditional deployment approval

The prior migration/whole-backend/Admin-render blockers are resolved within the
isolated verification scope. Exact backend line coverage is 84.68%.
There is no failing test in the final executed backend/frontend/browser gates.
This is NOT evidence that the entire autonomous service has completed E2E.

Remaining release gates:
1. Build and start the target Linux/amd64 Backend/Admin/Site images. The current
   build request was blocked by the tool before execution. Historical build
   timeouts are not re-labelled as current product failures. Local Node 22
   standalone success does not prove the Dockerfiles' Node 24 runtime parity.
2. A synthetic-provider operational rehearsal must connect real Beat scheduling,
   generation, publication and cache invalidation, measurement checkpoints,
   recovery, report creation and normal/exception notification delivery to a
   local capture endpoint. Seven canaries and an outbox row do not prove this.
3. Finish the browser happy path for downloading a genuinely validated director
   PDF and recording delivery/rescission/re-delivery. Backend PDF and delivery
   tests passed, but this browser path was not executed: the additional fixture
   write was blocked by the tool. No fabricated validation metadata was inserted.
4. Target-environment revision, migration head, IAM and secret alignment still
   require the separate approved rollout/preflight procedure. Production access
   and real provider/Slack calls were excluded, not silently treated as passed.

Do not weaken publication, report, production-URL, auth or tenant gates to obtain
approval. No deployment or push was performed. See the sibling validation JSON
and versioned evidence directory for exact counts and hashes.

## Preservation, cleanup and reproduction

The original Reputation worktree remained at d96bd2143ba509cd1fcae73f028286b6d938ae8e.
Its initial pending-file hashes, binary diff and Git status all matched at the end.
The initial target changes were backed up before edits. Two unused inherited
helpers were archived byte-for-byte under the evidence root rather than shipping
an unfinished report seed or the older, weaker offline-only harness:
`inherited-incomplete-report-seed.py` and `inherited-offline-helper.py`.

Only this run's API/Admin/worker sessions were terminated. PostgreSQL was stopped,
the owned Redis container was removed, and all five owned ports were confirmed
closed. Existing local databases, old test containers and the original worktree
were not stopped or changed. Database files, raw logs and fixture credentials
remain local; fixture credentials are not committed. Six selected evidence files,
including JUnit XML and UI screenshots, are versioned with SHA-256 in the JSON.

The harness refuses to run without the live, owned disposable PostgreSQL identity
in `/tmp/reputation-approval-current.json`. For this retained run, restart only
its `pgdata` on port 55584 with `LC_ALL=C LANG=C` and loopback binding; recreate
`reputation-approval-tqihijy1-redis` from cached `redis:7-alpine` on loopback port
56584 with persistence disabled. Never substitute an existing project/production DB.
A fresh run needs a new owned `/tmp/reputation-approval-*` directory, five distinct
high ports, and the five dedicated DB names declared by the runner.
