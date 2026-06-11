# ═══════════════════════════════════════════════════════════════════
# Re:putation — Cloud SQL PostgreSQL
# ═══════════════════════════════════════════════════════════════════

resource "google_sql_database_instance" "main" {
  name             = "${var.app_name}-db"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_16"

  settings {
    tier = var.db_instance_tier
    # DELIBERATE single-AZ for launch: ZONAL halves Cloud SQL cost vs REGIONAL.
    # Accepted risk: a zone outage takes the DB down until Google restores it
    # (backups + PITR below still protect against data loss). To move to HA
    # later, change to availability_type = "REGIONAL" — an in-place change that
    # triggers a brief restart, no data migration needed.
    availability_type     = "ZONAL"
    disk_size             = 10
    disk_type             = "PD_SSD"
    disk_autoresize       = true
    disk_autoresize_limit = 100

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
    }

    database_flags {
      name  = "max_connections"
      value = "100"
    }
  }

  deletion_protection = true

  depends_on = [google_service_networking_connection.private_vpc_connection]
}

resource "google_sql_database" "main" {
  name     = var.db_name
  instance = google_sql_database_instance.main.name
  project  = var.project_id
}
