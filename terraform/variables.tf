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
