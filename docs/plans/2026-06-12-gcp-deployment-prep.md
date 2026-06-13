# Re:putation GCP Deployment Prep - 2026-06-12

This is the deployment decision record for launching Re:putation on the existing
MotionLabs GCP estate. It is based on read-only `gcloud` inventory from
`wjlee@motionlabs.kr` and does not include secret values.

## Current GCP Inventory

- Active account/project: `wjlee@motionlabs.kr`, `mso-platform-481505`.
- Billing is enabled on `mso-platform-481505` under billing account
  `018EC4-B77DBE-803B20`.
- Existing production-like workloads are concentrated in `asia-northeast3`:
  `review-*`, `nmos-*`, `cs-*`, `kakaotalk-*`, `iam-server`, and `handdoc-api`.
- Existing Cloud SQL instances in `asia-northeast3` include `prod-review-db`
  and `nmos-db-instance` on private IP, plus several smaller public-IP
  Postgres instances.
- Existing Redis instances in `asia-northeast3` are `kakaotalk-redis` on
  `default` and `nmos-redis` on `nmos-vpc`; no `reputation-*` Redis exists.
- Existing load balancers and managed certificates serve product-specific
  domains such as `re-view.motionlabs.kr`, `api.re-view.motionlabs.kr`,
  `admin.re-view.motionlabs.kr`, `nmos.motionlabs.kr`, and CS/IAM domains.
- Cloud DNS has no managed zones in `mso-platform-481505`; product DNS is
  managed externally. `motionlabs.kr` is delegated to AWS Route53.
- `admin.reputation.motionlabs.kr`, `reputation.motionlabs.kr`, and
  `cname.reputation.motionlabs.kr` do not have production records yet.
- Required APIs are already enabled in the active project, including Cloud Run,
  Artifact Registry, Cloud SQL, Redis, Secret Manager, Compute, Service
  Networking, VPC Access, Monitoring, DNS, and Vertex AI.
- No `reputation` Artifact Registry repository, buckets, Cloud Run services,
  Cloud SQL instance, or Redis instance exists yet.

## Deployment Target Decision

Deploy production Re:putation into `mso-platform-481505` in
`asia-northeast3`.

Rationale:

- It matches the region and operating pattern of the existing MotionLabs
  services.
- It avoids a new-project fixed-cost duplicate while Re:putation is being
  launched.
- It keeps Korean customer traffic in the Seoul region for lower latency.
- Resource names are already prefixed by `reputation-*`, and no conflicts were
  found.

Do not deploy the current examples as-is with `project_id = "reputation-prod"`
and `region = "us-central1"` unless a separate production project is created
and funded first.

## Recommended Architecture

Use Terraform as the source of truth and `scripts/deploy.sh` for image rollouts
after infrastructure exists.

- Project: `mso-platform-481505`.
- Region/zone: `asia-northeast3`, `asia-northeast3-a`.
- Runtime:
  - `reputation-api` Cloud Run service behind the HTTPS LB for `/api/v1/*`.
  - `reputation-site` Cloud Run service behind the same HTTPS LB for the public
    site.
  - `reputation-admin` Cloud Run service behind the same HTTPS LB for
    `admin.reputation.motionlabs.kr`.
  - `reputation-worker`, `reputation-beat`, and `reputation-migrate` for Celery
    and Alembic.
- Data:
  - Dedicated private Cloud SQL Postgres 16 instance.
  - Dedicated Memorystore Redis BASIC 1GB instance.
  - Dedicated `reputation-images-*` and `reputation-reports-*` buckets in
    `ASIA-NORTHEAST3`.
- Network:
  - Dedicated `reputation-vpc` and `reputation-subnet` to avoid coupling with
    `prod-review-vpc`, `nmos-vpc`, or `default`.
  - Keep the current Terraform Serverless VPC Access connector for first
    launch, or migrate to Cloud Run Direct VPC egress before launch if reducing
    always-on connector cost is prioritized.
- Load balancing:
  - Create a dedicated Re:putation HTTPS LB and static IP.
  - Do not attach Re:putation hosts to the existing review LB unless the team
    deliberately accepts cross-product blast-radius and Terraform-state
    coupling.
- Domains:
  - `reputation.motionlabs.kr` is the platform/API base. It is not required as
    a marketed patient destination, but the system needs one stable platform
    host for API routing, revalidation, canonical fallback, and smoke tests.
  - `admin.reputation.motionlabs.kr` is the admin console host.
  - `cname.reputation.motionlabs.kr` is the stable customer-domain target shown
    inside Admin.
  - Hospital-owned domains are not separate app deployments. They route to the
    same HTTPS LB and `reputation-site` service; onboarding adds DNS and TLS for
    that host.

## Launch Cost Shape

Primary always-on costs:

- Cloud SQL instance CPU/memory/storage/backups.
- Memorystore Redis provisioned capacity.
- Serverless VPC Access connector min instances if the current Terraform
  connector path is retained.
- Global HTTPS load balancer forwarding/proxy/backend charges.
- Worker and beat Cloud Run services because their min instances are `1`.

Cost controls set in `terraform/terraform.mso-platform.example.tfvars`:

- API max instances capped at `2` for launch.
- Public site max instances stays `1` because Next ISR revalidation is
  single-instance-safe only in the current implementation.
- Admin max instances capped at `1`.
- Worker max instances capped at `2`; beat fixed at `1`.
- Public API/site/admin min instances stay `0`.

Relevant pricing references:

- Cloud Run: https://cloud.google.com/run/pricing
- Cloud SQL: https://cloud.google.com/sql/pricing
- Memorystore for Redis: https://cloud.google.com/memorystore/docs/redis/pricing
- Cloud Load Balancing: https://cloud.google.com/load-balancing/pricing

## Hard Blockers Before Customer Deployment

1. DNS must stop resolving to loopback:
   - `reputation.motionlabs.kr A/ALIAS -> <reputation-lb-ip>`
   - `admin.reputation.motionlabs.kr A/ALIAS -> <reputation-lb-ip>`
   - `cname.reputation.motionlabs.kr A/ALIAS -> <reputation-lb-ip>`
2. Secret Manager secret containers and versions must exist for:
   - `ANTHROPIC_API_KEY`
   - `OPENAI_API_KEY`
   - `GEMINI_API_KEY`
   - `SLACK_WEBHOOK_URL`
   - `ADMIN_SECRET_KEY`
   - `ADMIN_SESSION_SECRET`
   - `DB_PASSWORD`
   - `SITE_REVALIDATE_SECRET`
   - `SITE_BFF_SECRET`
3. Artifact Registry repository `reputation` must exist in `asia-northeast3`.
4. Bootstrap images must be pushed before the first full Terraform apply because
   Cloud Run services need valid image references.
5. Managed SSL certificates must become `ACTIVE` before real customer traffic is
   moved.

## Bootstrap Sequence

1. Copy `terraform/terraform.mso-platform.example.tfvars` to
   `terraform/terraform.tfvars` and fill image digests after image push.
2. Create the `asia-northeast3` Artifact Registry Docker repository
   `reputation`.
3. Create Secret Manager containers through Terraform or `terraform apply`, then
   add secret versions out of band. Do not put secret values in `.tfvars`.
4. Build and push initial backend/site/admin images to:
   `asia-northeast3-docker.pkg.dev/mso-platform-481505/reputation/...`.
5. Run Terraform using the existing GCS backend bucket with a Re:putation state
   prefix, for example:
   `terraform init -backend-config="bucket=mso-platform-terraform-state" -backend-config="prefix=reputation/prod"`.
6. `terraform apply` with immutable image digests.
7. Read `terraform output load_balancer_ip`.
8. Update AWS Route53 records for `reputation.motionlabs.kr`,
   `admin.reputation.motionlabs.kr`, and `cname.reputation.motionlabs.kr`.
9. Wait for DNS propagation and managed certificate `ACTIVE`.
10. Run `python3 scripts/check_public_dns.py reputation.motionlabs.kr admin.reputation.motionlabs.kr cname.reputation.motionlabs.kr`.
11. Run `scripts/deploy.sh all` without `SKIP_PUBLIC_DNS_PREFLIGHT` so the final
    rollout proves public DNS safety.
12. Verify:
    - `https://reputation.motionlabs.kr/api/v1/health/live`
    - `https://reputation.motionlabs.kr/`
    - `https://admin.reputation.motionlabs.kr/login`
    - Admin login, content publish, ISR revalidation, Slack notification, and
      public hospital page rendering.

## Pre-Deploy Improvement Candidates

- Replace Serverless VPC Access connector usage with Cloud Run Direct VPC egress
  if the team wants to reduce connector fixed cost. Existing services in the
  project already use Direct VPC egress annotations, so this is compatible with
  the estate, but it should be tested with Terraform before production apply.
- Add a custom least-privilege Vertex AI role to replace the broad
  `roles/aiplatform.user` default after the first launch.
- Add a real ops notification channel instead of the existing
  `admin@example.com` channels.
- Decide whether `db-custom-1-3840` is required for first customers. Lower tiers
  match some existing services but reduce headroom for concurrent API, worker,
  beat, and migration DB connections.
- Before scaling beyond a small number of hospital domains, replace per-domain
  Terraform-managed SSL cert entries with Certificate Manager automation. The
  current `customer_domains` path is acceptable for initial onboarding, but it
  still requires an infra apply for each newly connected hospital domain.
