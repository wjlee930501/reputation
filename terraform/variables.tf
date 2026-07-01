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
    (cloudsql.tf). Each backend instance may hold up to DB_POOL_SIZE +
    DB_MAX_OVERFLOW connections (5 + 5 = 10 by default, config.py), so
    instances x (pool + overflow) summed across api/worker/beat/migrate must stay
    under max_connections with headroom for superuser/maintenance sessions.
    Default budget: api 10x10=100 worst case alone — in practice API instances at
    max concurrently saturating their pools is rare, but if you raise this (or the
    pool sizes), raise max_connections or add pgbouncer first.
    Worker (5 instances) + beat (1) + migrate job also draw from the same budget.
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

# 병원이 별도 구입해 연결하는 커스텀 도메인(자기 도메인) 목록.
# 흐름: Admin에서 도메인 저장 → 병원이 DNS(CNAME 또는 A/ALIAS) 추가
#       → 이 목록에 추가 후 terraform apply (Certificate Manager cert + map entry 발급)
#       → Admin [DNS 확인] → site_live/ACTIVE.
# 주의: 고객 DNS가 LB로 향한 상태에서 apply해야 cert가 LB authorization으로 발급된다.
# cert 평면은 Certificate Manager certificate map(certmanager.tf)이라 proxy cert 한도(15)와
# 무관하며 수백~수천 도메인까지 확장된다.
# (설계: docs/plans/2026-06-23-certificate-manager-hybrid-domains.md)
variable "customer_domains" {
  description = "Hospital-owned custom domains served by the site (Certificate Manager managed cert + map entry)"
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

# HTTPS proxy의 cert 부착 방식 전환 플래그 (무중단 컷오버용).
#   false → 레거시 ssl_certificates(google_compute_managed_ssl_certificate) 나열.
#   true  → Certificate Manager certificate_map(certmanager.tf) 부착.
# 컷오버: 먼저 false로 apply해 cert map·와일드카드 cert를 깔고(DNS auth CNAME 추가 후
# 와일드카드 cert ACTIVE 대기) → true로 flip해 apply하면 메인 도메인 무중단 전환.
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
