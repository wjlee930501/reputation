# ═══════════════════════════════════════════════════════════════════
# Re:putation — Memorystore Redis
# ═══════════════════════════════════════════════════════════════════

resource "google_redis_instance" "main" {
  name           = "${var.app_name}-redis"
  project        = var.project_id
  region         = var.region
  tier           = "BASIC"
  memory_size_gb = var.redis_memory_size_gb

  redis_version      = var.redis_version
  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  # INFRA-5: Defense-in-depth beyond the private VPC — AUTH string + TLS in
  # transit for the Celery broker. OPT-IN (var.redis_auth_enabled, default false)
  # because enabling it is a COORDINATED change: a plaintext redis:// REDIS_URL
  # stops connecting the instant this flips. See the rollout note below. Redis is
  # already VPC-private (PRIVATE_SERVICE_ACCESS), so the default-off posture does
  # not expose the broker publicly.
  auth_enabled            = var.redis_auth_enabled
  transit_encryption_mode = var.redis_auth_enabled ? "SERVER_AUTHENTICATION" : "DISABLED"

  depends_on = [google_service_networking_connection.private_vpc_connection]
}

# ── Coordinated rollout to enable AUTH + TLS (INFRA-5) ──────────────
# Set var.redis_auth_enabled = true AND, in the same apply, update REDIS_URL for
# the api/worker/beat services (cloudrun.tf) — otherwise the plaintext
# redis://${host}:6379/0 will NO LONGER connect. Steps:
#   1. Read the generated AUTH string:  google_redis_instance.main.auth_string
#      (terraform output -raw redis_auth_string — sensitive), ideally store it in
#      Secret Manager rather than a plaintext env var.
#   2. Switch the scheme to rediss:// with the password:
#      rediss://:<AUTH_STRING>@<host>:6379/0
#   3. Ensure the Celery/redis client trusts the Memorystore server cert
#      (SERVER_AUTHENTICATION uses a Google-managed CA — redis-py needs
#      ssl_ca_certs or broker_use_ssl configured; verify connectivity in staging).
