# Hybrid Vercel + GCP + Supabase Deployment Prep

Decision for the 2026-06-20 launch: use Vercel for the Admin console and hospital-facing web app, GCP Cloud Run for the backend API plus Celery worker/beat, Supabase Postgres for the database, and existing GCS/Vertex asset flows. No marketing landing project/domain is part of this launch. The site repository may still contain `/landing` code, but external launch domains should attach only the hospital/platform surfaces needed for onboarding.

This is the optimal launch path for the current codebase: Vercel is efficient for Next.js frontends, but the backend has persistent Celery worker/beat processes, Redis-backed rate limits and schedules, and GCS/Vertex storage assumptions. Cloud Run fits those runtime needs without a storage/worker rewrite. Supabase remains useful as the database of record and avoids standing up Cloud SQL for launch.

## Decision Basis

- Vercel Functions are request-bounded and have maximum-duration behavior; they are not the right primary runtime for persistent Celery worker/beat processes.
- Cloud Run supports service/job deployment, configurable CPU/memory, scale-to-zero for request services, and instance-based billing where CPU outside requests is required.
- Supabase session pooler is the safer default for Cloud Run services and Celery workers. Transaction pooler is useful for many transient serverless connections, but it does not support prepared statements and is not the default backend launch choice here.

## Vercel Projects

- `reputation-admin`
  - Root directory: `admin`
  - Framework: Next.js
  - Build: `npm ci && npm run build`
  - Runtime/build env: `BACKEND_URL`, `NEXT_PUBLIC_BACKEND_URL`, `ADMIN_SECRET_KEY`, `ADMIN_SESSION_SECRET`, `SITE_BFF_SECRET`
  - External domain: `admin.reputation.motionlabs.kr`

- `reputation-site`
  - Root directory: `site`
  - Framework: Next.js
  - Build: `npm ci && npm run build`
  - Runtime/build env: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SITE_URL`, `NEXT_PUBLIC_BACKEND_URL`, `BACKEND_URL`, `SITE_BFF_SECRET`, `SITE_REVALIDATE_SECRET`, `NEXT_PUBLIC_GCP_STORAGE_BUCKET`
  - External domains: hospital/platform site domains only. Do not attach a separate marketing landing domain for this launch.

## GCP Cloud Run Backend

- `reputation-api`
  - Runtime: FastAPI from the existing backend Docker image.
  - Target: `bash scripts/deploy.sh backend` for the full backend runtime path, or `bash scripts/deploy.sh api` for API-only rollout.
  - Recommended launch env: `DB_CONNECTION_MODE=supabase`, `GCP_ATTACH_VPC_CONNECTOR=0` when using Supabase Postgres plus external managed Redis.

Do not use `bash scripts/deploy.sh all` for this hybrid launch. `all` remains the legacy full-Cloud-Run path that deploys Cloud Run frontends as well as backend services.

- `reputation-worker`
  - Runtime: Celery worker from the existing backend Docker image.
  - Keep `WORKER_MIN=1` for unattended onboarding automation.
  - Use a managed Redis URL. If using GCP Memorystore, set `GCP_ATTACH_VPC_CONNECTOR=1` and configure `VPC_CONNECTOR`.

- `reputation-beat`
  - Runtime: Celery beat/RedBeat scheduler from the existing backend Docker image.
  - Keep `BEAT_MIN=1`, `BEAT_MAX=1`.

Required GCP/Cloud Run Secret Manager entries for Supabase mode:

- `DATABASE_URL`
- `SYNC_DATABASE_URL`
- `REDIS_URL`
- `ADMIN_SECRET_KEY`
- `SITE_BFF_SECRET`
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `SLACK_WEBHOOK_URL`

## Supabase Postgres

Use Supabase Postgres as the database of record. For this hybrid launch, configure both SQLAlchemy URLs through the Supabase session pooler:

```text
DATABASE_URL=postgresql+asyncpg://SUPABASE_SESSION_POOLER_URL
SYNC_DATABASE_URL=postgresql+psycopg2://SUPABASE_SESSION_POOLER_URL
```

Run migrations through the Cloud Run migration target after production secrets are configured:

```bash
bash scripts/deploy.sh migrate
```

For direct Alembic execution from the `backend` directory, provide both database URLs because Alembic's online path creates the async engine from `DATABASE_URL`, while offline configuration reads `SYNC_DATABASE_URL`.

```bash
cd backend
APP_ENV=production DATABASE_URL="$DATABASE_URL" SYNC_DATABASE_URL="$SYNC_DATABASE_URL" uv run alembic upgrade head
```

Do not commit Supabase credentials. `.env.vercel-supabase.example` is only a key/shape template.

## Launch Prerequisites Outside This Repo

- GCP auth/project access: `gcloud auth login` must be refreshed before real deployment; current non-interactive checks cannot list or create Cloud Run resources.
- Redis: still required for SlowAPI rate limits, Celery broker/result backend, and RedBeat schedule state. Lowest fixed-cost launch path is an external managed `rediss://` provider; GCP Memorystore is more stable inside GCP but adds fixed cost and VPC connector setup.
- GCS/Vertex assets: image generation, uploads, and report downloads still use GCS and Google APIs. Keep GCS buckets/credentials available for onboarding.
- Domains: point `api.reputation.motionlabs.kr` to the GCP backend entrypoint, and attach only `admin.reputation.motionlabs.kr` plus hospital/platform web domains to Vercel.

## Preflight

Run the repository-owned dry run before pushing deployment env:

```bash
python3 scripts/check_vercel_supabase_deploy.py --json
python3 -m pytest scripts/test_vercel_supabase_preflight.py scripts/test_deploy_preflight.py scripts/test_deploy_runtime.py
```

This preflight is a deployment-preparation gate, not live deployment proof. It passes only when the two Vercel project roots, Cloud Run backend services, Supabase session-pooler URL shape, Secret Manager database-url mode, and landing exclusion note are present. It emits explicit warnings for Cloud Run worker/beat, Redis, GCS, and missing live deployment proof because those are intentional launch prerequisites, not hidden Vercel/Supabase features.

Before same-day onboarding cutover, collect the live proof separately:

- Vercel deployment URLs for `reputation-admin` and `reputation-site`.
- `gcloud run services describe` output for `reputation-api`, `reputation-worker`, and `reputation-beat`.
- Production `curl -i` responses for Admin, hospital site, and backend health endpoints.
- Production migration job or Alembic receipt against the Supabase database.

## Future GCP Migration Path

Keep the current environment names (`DATABASE_URL`, `SYNC_DATABASE_URL`, `REDIS_URL`, `GCP_STORAGE_BUCKET`, `GCS_REPORTS_BUCKET`, `ADMIN_BASE_URL`, `SITE_BASE_URL`) stable. That lets the service move between Supabase Postgres and Cloud SQL, or from Vercel frontends to Cloud Run frontends, without renaming application settings.
