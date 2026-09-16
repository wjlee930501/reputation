# Isolated operational release rehearsal

This harness is not a production entrypoint. It refuses non-test database settings.
All application containers use the `reputation-final-e2e-net` internal Docker network.
The only browser ingress forwards to fixed Admin/Site/API hosts. No Gcloud credentials,
production dotenv files, customer rows, live AI keys or Slack webhooks are mounted.

## What remains real

Linux/amd64 Dockerfile builds; production Next standalone Admin/Site; FastAPI BFF
contracts; PostgreSQL 16 initialized exclusively by Alembic; Redis/Celery/RedBeat;
SDK request/response parsing; content safety and provenance; signed dispatch and
leases; actual PDF rendering/validation; outbox HTTP transport and delivery history.

## Deliberate test boundaries

`capture.py` returns synthetic Anthropic/OpenAI/Gemini wire responses and hosts a
TLS Slack capture endpoint (first call 503, subsequent calls 200). GCS bytes are
stored on the disposable evidence volume. Test TLS forwards the ownership key
request to the REAL Site; it does not fabricate an ownership proof.
`runtime.py` advances the task business calendar through September/October 2026.
OS time, signature timestamps and monotonic/lease timers are not globally changed.
Beat uses registered production task/options, accelerated to two-second ticks.
Backoff age injection changes timestamps only, never run outcomes, attempt budgets,
report readiness, certification metadata, or append-only business facts.
The worker uses one `solo` process; production concurrency/load is not simulated.

## Retained verification stack

This dated rehearsal retains its owned containers and evidence under
`/private/tmp/reputation-approval-final-20260916-093831/container`.
`docker-base.json`, `qa.env`, `runtime-sha`, the test TLS certificate and
`ui-fixtures.json` remain local. Credentials/private keys are not versioned.
The parent `browser/` directory contains the locally installed Playwright package.
Start only containers bearing `reputation.validation=final-e2e`; do not use the
original Reputation compose stack or its existing databases.

The tested public origin is `https://site.example.test`, matching the Site's
build-time NEXT_PUBLIC_SITE_URL. Its synthetic tenant host is
`e2e-clinic-0.site.example.test`, resolved ONLY inside the internal Docker network
through the TLS capture proxy. SITE_REVALIDATE_URL remains the private Site URL.
Public canonical, health, crawler outputs and IndexNow ownership are checked
against this same host. Changing just the runtime public URL does not rewrite
Next's already-compiled NEXT_PUBLIC values.

```sh
cd /Users/woojinlee/projects/Reputation-geo-hardening
E=/private/tmp/reputation-approval-final-20260916-093831/container
# Start the retained DB/queue/Site/Admin/ingress; renew expired TEST TLS first.
docker start reputation-final-e2e-db reputation-final-e2e-queue \
  reputation-final-e2e-site reputation-final-e2e-admin reputation-final-e2e-ingress
# Back up this disposable DB and wire/evidence files before beginning a new round.
python3 scripts/release_e2e/reset_owned.py "$E"
python3 scripts/release_e2e/drive.py "$E" pipeline
python3 scripts/release_e2e/replay.py "$E"
node scripts/release_e2e/browser.mjs "$E"
python3 scripts/release_e2e/chaos.py "$E"
docker exec reputation-final-e2e-api python /qa/surface.py
python3 scripts/release_e2e/stock_images.py "$E"
```

Completed reference run: `docs/reviews/2026-09-16-operational-e2e.md` and its
validation JSON. The deployment application image does not copy this directory.
The capture endpoint serves image bytes read back from the storage adapter's
persisted object, rather than returning an unrelated image fixture.
`replay.py` verifies repeated complete schedules do not purchase AI work again.
`stock_images.py` uses a separate empty database/Redis DB and the unmodified image
entrypoint for migration/API/prefork Worker/Beat; it cleans up only its unique
containers. APP_ENV=test is retained and no Gcloud credentials are mounted.
After verification, the retained main stack is stopped, not deployed; restart the
named owned containers above to reproduce. Raw DB/wire archives and test credentials
stay local and are not committed. Unused manually assembled report prototypes were
archived: the passing browser lane uses the actual worker-generated report instead.
