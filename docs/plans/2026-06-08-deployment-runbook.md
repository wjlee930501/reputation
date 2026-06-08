# Re:putation — Deployment Runbook (post-hardening)

> For deploying the production-hardening change set (main @ `01c5636`). Nothing is
> deployed yet. Follow in order. ⛔ = hard blocker that crash-loops / locks out if skipped.

Deployment is **manual** (CI does NOT deploy). Backend → GCP Cloud Run; infra →
Terraform; frontends → Vercel. gcloud is authed as `wjlee@motionlabs.kr`, project
`mso-platform-481505`, region `us-central1`.

---

## 0. ⛔ The one thing that will crash-loop if you skip it

The backend now **fails fast at boot** (`config._validate_production_config`) unless,
in production, ALL of these hold:
- `ALLOWED_ORIGINS` = real https origins (admin + site domains), **no** `*`, **no** localhost.
- `TRUSTED_PROXY_IPS` = real LB/proxy CIDRs, **not** localhost-only, **not** `0.0.0.0/0`.
  Default is the GCP global LB range `130.211.0.0/22,35.191.0.0/16` — **verify** it matches
  the GFE range that actually fronts your Cloud Run (else client-IP keying is coarse, not broken).
- `DATABASE_URL`/`SYNC_DATABASE_URL` resolve (or `DB_*` secret parts present).

Set these in whichever deploy path you use (§2). If they're wrong, the API container
restarts forever with `ValueError: Insecure production config`.

---

## 1. Pre-flight (verify, don't change anything yet)

- [ ] `gcloud config get-value project` → `mso-platform-481505`; `gcloud auth list` active.
- [ ] **Secret Manager** has all 7 required secrets (deploy aborts if any missing):
      `ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY SLACK_WEBHOOK_URL ADMIN_SECRET_KEY ADMIN_SESSION_SECRET DB_PASSWORD`
      `for s in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY SLACK_WEBHOOK_URL ADMIN_SECRET_KEY ADMIN_SESSION_SECRET DB_PASSWORD; do gcloud secrets describe $s >/dev/null 2>&1 && echo "✓ $s" || echo "✗ MISSING $s"; done`
      (optional: `SITE_REVALIDATE_SECRET`)
- [ ] `SLACK_WEBHOOK_URL` secret host is `hooks.slack.com` (new allowlist rejects other hosts).
- [ ] Decide the deploy path (§2) — **terraform and deploy.sh both define the Cloud Run
      services and will fight if mixed.** Pick one as the source of truth.
- [ ] Take a Cloud SQL backup / snapshot before migrating (§3).

---

## 2. Choose ONE service-config source of truth

**Path A — Terraform (recommended; declarative, my Wave-3 env is already wired here).**
`terraform/cloudrun.tf` already sets `TRUSTED_PROXY_IPS`, `ALLOWED_ORIGINS` (derived from
`var.domain`/`var.admin_subdomain`), `LOG_LEVEL`, `LOG_JSON`, `PUBLIC_SITE_RATE_LIMIT`.
- [ ] Set in `terraform.tfvars`: `domain`, `admin_subdomain` (→ ALLOWED_ORIGINS), and
      override `allowed_origins`/`trusted_proxy_ips` if the derived values are wrong.
- [ ] Build+push the image first (§4), then set `var.api_image` to the immutable digest.
- [ ] `cd terraform && terraform plan` → review (Cloud Armor rate-ban, image, env, SA). Then `terraform apply`.
      Leave `redis_auth_enabled=false` for now (enabling it needs the coordinated REDIS_URL change — see backlog INFRA-5).

**Path B — `scripts/deploy.sh` (imperative; env from `.env.production`).**
deploy.sh passes non-secret env via `--env-vars-file` built from `.env.production`
(secrets come from Secret Manager). If you use this path you MUST add the new vars to
`.env.production` or the boot check fails:
```
ALLOWED_ORIGINS=https://admin.<domain>,https://<domain>
TRUSTED_PROXY_IPS=130.211.0.0/22,35.191.0.0/16
LOG_LEVEL=INFO
LOG_JSON=true
PUBLIC_SITE_RATE_LIMIT=300/minute;6000/hour
SLACK_WEBHOOK_ALLOWED_HOSTS=hooks.slack.com
```
(Use `.env.production.example` as the template — it now documents all of these.)

> Don't run both paths against the same services. If infra (LB/SQL/Redis/Armor/SA/secrets)
> is Terraform-managed but you redeploy code with deploy.sh, that's fine — just keep the
> Cloud Run *env* consistent in both.

---

## 3. ⛔ Database migration (run BEFORE the new code)

Applies `0023` (hospitals JSON cols → NOT NULL + `'[]'`, 4 covering FK indexes) and
`0024` (admin_audit_logs append-only: drops the hospital_id FK, installs an
UPDATE/DELETE/TRUNCATE-blocking trigger). Both are backward-compatible with the currently
running code (it only inserts audit rows and uses Python `default=list`), so a rolling
deploy is safe.

- [ ] **Back up Cloud SQL first** (the trigger + NOT NULL are easy to reverse via
      `alembic downgrade`, but snapshot anyway).
- [ ] `bash scripts/deploy.sh migrate`  (builds+pushes the image, then runs a Cloud Run
      job with `SERVICE=migrate` → `alembic upgrade head`). Or `make deploy-migrate`.
- [ ] Verify: the job logs end at `Running upgrade 0023... -> 0024...`; spot-check
      `SELECT tgname FROM pg_trigger WHERE tgname LIKE 'admin_audit_logs_block%';` returns 2 rows.

Rollback: `alembic downgrade 0022` (re-adds the FK, drops the trigger, makes JSON cols
nullable again). Run via a one-off job with `SERVICE` overridden to run that command.

---

## 4. Build + push the image, deploy services

The Dockerfile is now multi-stage / non-root / digest-pinned base (verified: builds and
runs, weasyprint+lxml import OK).
- [ ] `bash scripts/deploy.sh all`  → builds `linux/amd64`, pushes to Artifact Registry,
      deploys `reputation-api` (ingress `internal-and-cloud-load-balancing`),
      `reputation-worker`, `reputation-beat`. (Or `make deploy-all`.)
      - For Path A, instead capture the pushed digest and `terraform apply` with `var.api_image=<digest>`.
- [ ] Watch the API revision come healthy. **If it crash-loops, it's almost certainly §0**
      (missing/invalid ALLOWED_ORIGINS or TRUSTED_PROXY_IPS) — check the logs for
      `Insecure production config`.

---

## 5. ⛔ Seed the first admin user (console is locked out otherwise)

`admin_users` is empty in prod → nobody can log into the admin console. The `make
admin-create-owner` target is local-docker only; in prod run the module against the prod DB:
- [ ] As a one-off Cloud Run job (reuse the migrate job image) or via Cloud SQL proxy session:
      `ADMIN_EMAIL=<you@motionlabs.kr> ADMIN_PASSWORD=<min 14 chars> ADMIN_NAME=<name> python -m app.utils.admin_user create-owner`
- [ ] Confirm: login to the admin app with those creds returns a session.

---

## 6. Frontends (Vercel)

If Vercel is connected to the GitHub repo, the push to `main` may have **already
triggered** site + admin deploys — check the Vercel dashboard. The new public-site CSP
ships in `site/next.config.mjs` (build-time).
- [ ] Verify Vercel env: **admin** → `ADMIN_SESSION_SECRET`, `ADMIN_SECRET_KEY`, `BACKEND_URL`;
      **site** → `SITE_REVALIDATE_SECRET`, the public API base, optional `NEXT_PUBLIC_*`.
- [ ] After deploy, load a clinic page and confirm it renders (no CSP console errors —
      fonts/images/JSON-LD). Submit a test lead end-to-end.

---

## 7. Post-deploy smoke verification

- [ ] `GET /health/live` and `/health/ready` (ready hits the DB) → 200.
- [ ] Public site page renders; security headers present (`curl -I` → CSP, HSTS, nosniff).
- [ ] Admin login works; an admin action writes an audit row; confirm the row can't be
      UPDATEd/DELETEd (`UPDATE admin_audit_logs ...` → `append-only` error).
- [ ] A public read endpoint returns; hammering one IP eventually 429s (per-IP limit) and
      Cloud Armor rate-ban engages at the LB.
- [ ] Logs are JSON in Cloud Logging with `severity` + `request_id`.
- [ ] `SLACK_WEBHOOK_URL` test alert delivers (host allowlist passes for hooks.slack.com).

---

## 8. Known limitations carried into this deploy (not blockers)

Tracked in `2026-06-08-production-hardening-backlog.md`:
- **CDX-M1** public-lead visitor-IP is coarse through the Vercel→LB chain (consent_ip =
  edge ingress, lead rate-limit per-egress). Not spoofable; Cloud Armor covers abuse.
- **CDX-M2** operator free-text `conversion_note` PII isn't scrubbed from `onboarding_note` on erase.
- **CDX-M3** admin login throttle is per-process (Vercel serverless), not global.
- **INFRA-5** Redis AUTH/TLS is opt-in `var.redis_auth_enabled` (default off) — enabling
  needs a coordinated `rediss://` REDIS_URL + broker TLS CA in the same apply.
- **AUTH-3** admin API is reachable directly via the LB (X-Admin-Actor forgeable to a
  shared-key holder); proper fix is network isolation. Audit trail is append-only either way.

## 9. Rollback summary
- Code: redeploy the previous image digest (Cloud Run keeps revisions — `gcloud run services update-traffic reputation-api --to-revisions=<prev>=100`).
- DB: `alembic downgrade 0022`.
- Infra: `terraform apply` the previous state / revert the tfvars + `git revert`.
