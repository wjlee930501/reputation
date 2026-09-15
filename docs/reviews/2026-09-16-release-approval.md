# GEO release-candidate verification — 2026-09-16

Status: IN PROGRESS. This record does not authorize production deployment.
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
