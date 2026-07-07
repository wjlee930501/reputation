# ═══════════════════════════════════════════════════════════════════
# Re:putation — Terraform Variables
# ═══════════════════════════════════════════════════════════════════

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "asia-northeast3"
}

variable "zone" {
  description = "GCP Zone for zonal resources"
  type        = string
  default     = "asia-northeast3-a"
}

# ── Naming ────────────────────────────────────────────────────────
variable "app_name" {
  description = "Application name prefix"
  type        = string
  default     = "reputation"
}

# ── Cloud Run ─────────────────────────────────────────────────────
variable "api_image" {
  description = <<-EOT
    Required immutable digest image for the api/worker/beat Cloud Run services
    (for example
    "<region>-docker.pkg.dev/<project>/reputation/reputation@sha256:<digest>").
    CI/CD should set this to the digest it just pushed so applies and rollbacks
    are reproducible.
  EOT
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
    error_message = "api_image must be an immutable @sha256 digest image reference."
  }
}

variable "api_memory" {
  description = "API service memory"
  type        = string
  default     = "512Mi"
}

variable "api_cpu" {
  description = "API service CPU count"
  type        = number
  default     = 1
}

variable "api_min_instances" {
  description = "API minimum instances (0 = scale to zero)"
  type        = number
  default     = 0
}

variable "api_max_instances" {
  description = <<-EOT
    API maximum instances. DB CONNECTION BUDGET: Cloud SQL max_connections is 100
    (cloudsql.tf), and the worst-case concurrent total must stay under
    max_connections x 0.9 = 90 (10% headroom for superuser/maintenance sessions).
    The budget is split between the async API pool and the sync worker pool
    (config.py):
      api    = api_max_instances x (DB_POOL_SIZE + DB_MAX_OVERFLOW)
             = 10 x (3 + 2) = 50
      worker = worker_max_instances x CELERY_CONCURRENCY
               x (DB_WORKER_POOL_SIZE + DB_WORKER_MAX_OVERFLOW)
             = 5 x 2 x (2 + 2) = 40
      total  = 50 + 40 = 90 <= 90.
    The sync worker engine is created lazily per Celery prefork child, so the
    worker pool multiplies by CELERY_CONCURRENCY (cloudrun.tf), not by instance
    count alone. beat holds no DB connections; the migrate Job runs one-shot
    before deploy and does not overlap the traffic peak. If you raise this (or any
    pool size / worker_max_instances / CELERY_CONCURRENCY), raise max_connections
    or add pgbouncer first, then re-verify the invariant with
    scripts/check_db_connection_budget.py (the CI/preflight budget guard).
  EOT
  type        = number
  default     = 10
}

# ── Frontend (Next.js on Cloud Run) ───────────────────────────────
variable "site_image" {
  description = <<-EOT
    Required immutable digest image for the public site (Next.js) Cloud Run
    service. NOTE: NEXT_PUBLIC_* values are inlined at image BUILD time (see
    site/Dockerfile build args) — rebuild the image when the domain changes.
  EOT
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.site_image))
    error_message = "site_image must be an immutable @sha256 digest image reference."
  }
}

variable "admin_image" {
  description = "Required immutable digest image for the admin console (Next.js) Cloud Run service."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.admin_image))
    error_message = "admin_image must be an immutable @sha256 digest image reference."
  }
}

variable "site_memory" {
  type    = string
  default = "512Mi"
}

variable "site_cpu" {
  type    = number
  default = 1
}

variable "site_min_instances" {
  type    = number
  default = 0
}

variable "site_max_instances" {
  description = <<-EOT
    Public site max instances. DEFAULT 1 ON PURPOSE: Next.js on-demand ISR
    revalidation (POST /api/revalidate, fired on content publish) only clears the
    cache of the instance that receives it — with N>1 instances, other instances
    serve stale pages until the time-based revalidate (3600s) expires. Raise this
    only if you accept up-to-1h staleness after publish, or move the ISR cache to
    a shared backend (custom cacheHandler) first.
  EOT
  type        = number
  default     = 1
}

variable "admin_memory" {
  type    = string
  default = "512Mi"
}

variable "admin_cpu" {
  type    = number
  default = 1
}

variable "admin_min_instances" {
  type    = number
  default = 0
}

variable "admin_max_instances" {
  type    = number
  default = 2
}

variable "worker_memory" {
  type    = string
  default = "1Gi"
}

variable "worker_cpu" {
  type    = number
  default = 1
}

variable "worker_min_instances" {
  type    = number
  default = 1
}

variable "worker_max_instances" {
  type    = number
  default = 5
}

variable "beat_memory" {
  type    = string
  default = "512Mi"
}

variable "beat_cpu" {
  type    = number
  default = 1
}

variable "beat_min_instances" {
  description = "Beat scheduler minimum instances. Keep at least one scheduler running."
  type        = number
  default     = 1
}

variable "beat_max_instances" {
  description = "Beat scheduler maximum instances. Keep exactly one scheduler by default."
  type        = number
  default     = 1
}

# ── Cloud SQL ─────────────────────────────────────────────────────
variable "db_instance_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-custom-1-3840"
}

variable "db_edition" {
  description = "Cloud SQL edition. Keep ENTERPRISE with db-custom-* tiers; ENTERPRISE_PLUS requires db-perf-optimized-* tiers."
  type        = string
  default     = "ENTERPRISE"

  validation {
    condition     = contains(["ENTERPRISE", "ENTERPRISE_PLUS"], var.db_edition)
    error_message = "db_edition must be ENTERPRISE or ENTERPRISE_PLUS."
  }
}

variable "db_name" {
  type    = string
  default = "reputation"
}

variable "db_user" {
  type    = string
  default = "reputation"
}

# ── Redis (Memorystore) ───────────────────────────────────────────
variable "redis_memory_size_gb" {
  type    = number
  default = 1
}

variable "redis_version" {
  type    = string
  default = "REDIS_7_0"
}

# ── Load Balancer ─────────────────────────────────────────────────
variable "domain" {
  description = "Primary platform/API domain for HTTPS LB (e.g., reputation.motionlabs.kr)"
  type        = string
}

variable "admin_subdomain" {
  description = "Admin subdomain (e.g., admin.reputation.motionlabs.kr)"
  type        = string
  default     = ""
}

variable "cname_target" {
  description = "Stable DNS target shown to hospitals for custom domain CNAME records."
  type        = string
  default     = "cname.reputation.motionlabs.kr"
}

variable "enable_http_redirect" {
  description = "Create the port 80 forwarding rule that redirects HTTP to HTTPS. Disable temporarily if the project global forwarding-rule quota is exhausted."
  type        = bool
  default     = true
}

# 병원이 별도 구입해 연결한 기존 커스텀 도메인(자기 도메인) 목록.
# 현재 이 목록은 legacy google_compute_managed_ssl_certificate를 HTTPS proxy에
# 직접 붙이는 경로다. Certificate Manager map이 붙으면 map이 이 direct cert보다
# 우선하므로, 기존 라이브 도메인을 map entry로 옮기기 전에는 이 목록만으로는
# map cutover가 안전하지 않다.
variable "customer_domains" {
  description = "Legacy hospital-owned custom domains served by direct managed SSL certs"
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for d in var.customer_domains :
      can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", d))
    ])
    error_message = "customer_domains must be lowercase hostnames without scheme/path/port."
  }
}

# Certificate Manager certificate map으로 실제 서빙할 고객 도메인.
# 신규 고객 도메인은 고객 DNS가 LB로 향하고 발급 대기 중인 짧은 구간을 허용할 때만
# 여기에 넣는다. 이미 classic cert로 라이브 중인 도메인은 별도 DNS authorization이나
# 유지보수 창 없이 여기에 넣으면 TLS가 일시적으로 깨질 수 있다.
variable "certificate_map_customer_domains" {
  description = "Hospital-owned custom domains that should receive Certificate Manager map entries"
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for d in var.certificate_map_customer_domains :
      can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", d))
    ])
    error_message = "certificate_map_customer_domains must be lowercase hostnames without scheme/path/port."
  }
}

# HTTPS proxy의 cert 부착 방식 전환 플래그 (무중단 컷오버용).
#   false → 레거시 ssl_certificates(google_compute_managed_ssl_certificate) 나열.
#   true  → Certificate Manager certificate_map(certmanager.tf) 부착.
# 컷오버: 먼저 false로 apply해 cert map·와일드카드 cert를 깔고(DNS auth CNAME 추가 후
# 와일드카드 cert ACTIVE 대기) → 모든 라이브 hostname이 ACTIVE map entry로 커버될 때만
# true로 flip한다.
variable "use_certificate_map" {
  description = "Attach Certificate Manager certificate_map to the HTTPS proxy instead of legacy ssl_certificates"
  type        = bool
  default     = false
}

# ── DNS (optional) ────────────────────────────────────────────────
variable "dns_zone_name" {
  description = "Cloud DNS managed zone name (skip if using external DNS)"
  type        = string
  default     = ""
}

# ── Storage ───────────────────────────────────────────────────────
variable "images_bucket_location" {
  type    = string
  default = "US"
}

variable "reports_bucket_location" {
  type    = string
  default = "US"
}

variable "site_revalidate_url" {
  description = "Site revalidation webhook URL. Defaults to https://<domain>/api/revalidate."
  type        = string
  default     = ""
}

# ── Backend runtime / security env ─────────────────────────────────
variable "allowed_origins" {
  description = <<-EOT
    Browser-facing CORS allowlist for the API (ALLOWED_ORIGINS). Comma-separated
    HTTPS origins for the production admin and site domains. CORS runs with
    credentials, so this must never be "*" and must not contain localhost in
    production. Defaults to the public site + admin origins derived from
    var.domain / var.admin_subdomain; override if those differ.
  EOT
  type        = list(string)
  default     = []
}

variable "trusted_proxy_ips" {
  description = <<-EOT
    CIDR ranges of trusted reverse-proxy hops (TRUSTED_PROXY_IPS). The backend
    parses X-Forwarded-For RIGHT-TO-LEFT and returns the first entry NOT in these
    ranges as the real client IP (for rate-limit keying + PIPA consent_ip). These
    MUST be the actual proxy/LB ranges that front Cloud Run, NOT 0.0.0.0/0 — a
    catch-all would mark every hop trusted and make the client IP spoofable
    (backend boot now rejects 0.0.0.0/0 in production). Default is the GCP global
    external Application Load Balancer / GFE range; verify for your setup.
  EOT
  type        = list(string)
  default     = ["130.211.0.0/22", "35.191.0.0/16"]
}

variable "public_site_rate_limit" {
  description = "PUBLIC_SITE_RATE_LIMIT for unauthenticated public site read endpoints."
  type        = string
  default     = "300/minute;6000/hour"
}

variable "sentry_dsn" {
  description = <<-EOT
    Optional Sentry DSN for backend error tracking (SENTRY_DSN). When empty
    (default), no SENTRY_DSN env is set on the Cloud Run services and the app
    skips Sentry initialization entirely.
  EOT
  type        = string
  default     = ""
}

# ── Redis hardening (INFRA-5) ─────────────────────────────────────
variable "redis_auth_enabled" {
  description = <<-EOT
    Enable Memorystore AUTH + TLS (SERVER_AUTHENTICATION). OFF by default because
    it is a coordinated change: the plaintext redis:// REDIS_URL in cloudrun.tf
    must switch to rediss://:<auth>@host:6379/0 (with broker TLS CA config) in the
    same apply or the Celery broker stops connecting. Redis is already VPC-private,
    so default-off does not expose the broker. See redis.tf rollout note.
  EOT
  type        = bool
  default     = false
}

# ── IAM (least privilege) ─────────────────────────────────────────
variable "aiplatform_role" {
  description = <<-EOT
    Vertex AI role granted to the app service account (INFRA-7). The app only
    needs Imagen 3 prediction. Defaults to the broad predefined roles/aiplatform.user;
    override with a custom role (e.g. "projects/<project>/roles/imagenPredictor"
    carrying only aiplatform.endpoints.predict) to follow least-privilege.
  EOT
  type        = string
  default     = "roles/aiplatform.user"
}
