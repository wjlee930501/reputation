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

  depends_on = [google_service_networking_connection.private_vpc_connection]
}
