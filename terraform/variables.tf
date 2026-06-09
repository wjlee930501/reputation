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
  default     = "us-central1"
}

variable "zone" {
  description = "GCP Zone for zonal resources"
  type        = string
  default     = "us-central1-a"
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
    Container image for the api/worker/beat Cloud Run services. Prefer an
    immutable digest reference (e.g.
    "us-central1-docker.pkg.dev/<project>/reputation/reputation@sha256:<digest>")
    so deploys and rollbacks are reproducible. The CI/CD pipeline should set
    this to the digest it just pushed. A floating tag is allowed as a fallback
    but is discouraged because :latest is mutable. The default leaves the value
    empty so the project-derived default below is used; set explicitly to a
    digest in production.
  EOT
  type        = string
  default     = ""
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
  type    = number
  default = 10
}

# ── Frontend (Next.js on Cloud Run) ───────────────────────────────
variable "site_image" {
  description = <<-EOT
    Container image for the public site (Next.js) Cloud Run service. Prefer an
    immutable digest. NOTE: NEXT_PUBLIC_* values are inlined at image BUILD time
    (see site/Dockerfile build args) — rebuild the image when the domain changes.
  EOT
  type        = string
  default     = ""
}

variable "admin_image" {
  description = "Container image for the admin console (Next.js) Cloud Run service. Prefer an immutable digest."
  type        = string
  default     = ""
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
  default = "256Mi"
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
  description = "Primary domain for HTTPS LB (e.g., reputation.co.kr)"
  type        = string
}

variable "admin_subdomain" {
  description = "Admin subdomain (e.g., admin.reputation.co.kr)"
  type        = string
  default     = ""
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
