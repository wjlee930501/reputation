# Isolated integrity release rehearsal

Manager invocation, from the repository root:

```sh
bash scripts/integrity_rehearsal/run.sh
```

Requires a local Docker daemon, Docker Buildx, Compose supporting `up --wait`, Bash, and
internet access for image/dependency build pulls. No global installs. Linux CI
uses a 17-minute execution limit (20-minute job). A cold image build can dominate
runtime. An initial Linux/amd64 run completed the queue and worker-loss scenarios.
Use the frozen commit CI and run artifacts, not this document, for the current verdict.

The runner creates a unique Compose project and fresh build context from an
allowlist of production backend inputs. It builds the unmodified production
Dockerfile and runs disposable PostgreSQL 16.4 and Redis 7.4.0. Database storage is
tmpfs; no existing stack or retained `/tmp` state is used. There are no host ports,
Docker socket mounts, cloud credentials, host env propagation, or external
runtime networks. Docker CLI runs with an empty per-run config (no registry login)
and cleared environment; it explicitly retains only the selected local Unix Docker socket. Runtime
network is `internal: true`. Image pulls/build dependency downloads happen before
runtime isolation. Base tags may move; exact resolved build logs are retained.

`APP_ENV=production` activates real startup and dispatch safety validation, but
**every configuration value is a public test-only fixture**. No production env
file is loaded. Cloud project is empty, dotenv disabled, ADC points to a nonexistent
file, and metadata points to a closed loopback port. API, worker, and beat run
stock `SERVICE` entrypoints. Worker pool/concurrency and registration are asserted
via real Celery inspection. Beat is stopped after startup/canary checks so scheduled
business tasks cannot race fixture scenarios. No Celery tasks are eagerly run.

## Assertions executed by the harness

- Seven signed canaries delivered on seven actual Redis queues, with matching task
  IDs, database/Redis/outbox checks, and current-release canary records.
- Real registered image-refresh/reconciler tasks; unsigned protocol-v2 broker
  message rejected by the production dispatch gate.
- Two PostgreSQL sessions compete on row lock and live lease. Expired claim is
  replaced; old-token success and failure writebacks cannot overwrite the owner.
- Fifty terminal candidates followed by an eligible 51st row. A signed image-refresh
  delivery must reach the HTTP provider fixture, record failure, retain fallback
  marker, and leave certification absent. Two further actual task deliveries must
  skip all rows without further provider HTTP calls. A second fixture gives the
  first fifty rows live leases; the next available claim must still execute. Retry deadline is moved into
  the future explicitly to avoid crossing a real KST sweep boundary.
- Transactional public-surface intent created through the application service,
  real reconciler delivery, real callback request held at the HTTP boundary, then
  worker container SIGKILL. Pending durable state must survive. Test advances its
  heartbeat past the retry deadline, restarts the worker, dispatches reconciliation
  through Redis, and requires a second callback plus persisted `SUCCEEDED` /
  `ACCEPTED`, preserving `page_visibility_verified=false`.

## Fixture boundary and provenance

`sitecustomize.py` changes only OpenRouter's transport base URL to the internal
fixture. It does not replace task bodies, broker, database sessions, safety gates,
policy review, certification, or writeback functions. The HTTP provider always
returns 503, exercising the real image pipeline's failure handling. The site
fixture checks the callback secret and test tenant paths and records receipts in
Redis. Unexpected HTTP paths fail and are recorded.

Database rows are **TEST ONLY, intentionally uncertified and not publishable**.
Their PUBLISHED status and fallback marker exist solely to enter the refresh
selector; they are not evidence of publication or patient-visible content. No
synthetic image hashes, approval outcomes, policy certifications, or PASS review
metadata are inserted. Scenario output is emitted only after assertions.

## Explicit missing coverage

No successful image generation/policy review/upload/certified replacement; no
crash precisely between an image-success commit and its immediate callback; no
image-refresh success-path atomicity proof. Durable recovery uses a separately
committed application public-surface intent and loss during a callback. Claims
compete through real DB sessions, not simultaneous successful provider workers.
No invalid signature/expired signature matrix beyond unsigned-message rejection.
No long-duration beat schedule/delivery proof, frontend/browser/CDN visibility,
cloud storage/IAM, external provider correctness, or production readiness claim.

Evidence is under `artifacts/<unique-run>/`: assertion JSONL, build/container logs,
resolved Compose config, container status, cleanup log, and exit status. Any failed
assertion or command fails the run. EXIT/INT/TERM clean only this project's resources;
SIGKILL of the runner itself cannot execute cleanup. Use the recorded Compose
project name to remove leftovers in that case. Historical `scripts/release_e2e/`
is neither imported nor changed.
