# Operational E2E completion — work in progress

Baseline: 6b0c86f67847c0a1b494e8a66fb902f46ed1ce0e.
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
