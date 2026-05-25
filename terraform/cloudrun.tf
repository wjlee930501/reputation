# ═══════════════════════════════════════════════════════════════════
# Re:putation — Cloud Run Services
# ═══════════════════════════════════════════════════════════════════

# ── API Service ────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "api" {
  name     = "${var.app_name}-api"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.app.email

    containers {
      image = "us-central1-docker.pkg.dev/${var.project_id}/reputation/reputation:latest"

      env {
        name  = "SERVICE"
        value = "api"
      }
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name = "DATABASE_URL"
        value = "postgresql+asyncpg://${var.db_user}:${coalesce(var.db_password, random_password.db.result)}@/reputation?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
      }
      env {
        name = "SYNC_DATABASE_URL"
        value = "postgresql://${var.db_user}:${coalesce(var.db_password, random_password.db.result)}@/reputation?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.main.host}:6379/0"
      }
      env {
        name  = "GCP_STORAGE_BUCKET"
        value = google_storage_bucket.images.name
      }
      env {
        name  = "GCS_REPORTS_BUCKET"
        value = google_storage_bucket.reports.name
      }

      resources {
        limits = {
          cpu    = var.api_cpu
          memory = var.api_memory
        }
        cpu_idle          = false
        startup_cpu_boost = true
      }

      ports {
        container_port = 8000
      }

      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 10
        period_seconds        = 10
        failure_threshold     = 3
        http_get {
          path = "/health/ready"
          port = 8000
        }
      }

      liveness_probe {
        http_get {
          path = "/health/live"
          port = 8000
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_version.anthropic_api_key,
  ]
}

# ── Worker Service ─────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "worker" {
  name     = "${var.app_name}-worker"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.app.email

    containers {
      image = "us-central1-docker.pkg.dev/${var.project_id}/reputation/reputation:latest"

      env {
        name  = "SERVICE"
        value = "worker"
      }
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "CELERY_CONCURRENCY"
        value = "2"
      }
      env {
        name  = "CELERY_MAX_TASKS_PER_CHILD"
        value = "50"
      }
      env {
        name = "DATABASE_URL"
        value = "postgresql+asyncpg://${var.db_user}:${coalesce(var.db_password, random_password.db.result)}@/reputation?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
      }
      env {
        name = "SYNC_DATABASE_URL"
        value = "postgresql://${var.db_user}:${coalesce(var.db_password, random_password.db.result)}@/reputation?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.main.host}:6379/0"
      }
      env {
        name  = "GCP_STORAGE_BUCKET"
        value = google_storage_bucket.images.name
      }
      env {
        name  = "GCS_REPORTS_BUCKET"
        value = google_storage_bucket.reports.name
      }

      resources {
        limits = {
          cpu    = var.worker_cpu
          memory = var.worker_memory
        }
        cpu_idle = false
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    scaling {
      min_instance_count = var.worker_min_instances
      max_instance_count = var.worker_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
  }

  depends_on = [
    google_project_service.services,
  ]
}

# ── Beat Service (Celery Beat Scheduler) ──────────────────────────
resource "google_cloud_run_v2_service" "beat" {
  name     = "${var.app_name}-beat"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.app.email

    containers {
      image = "us-central1-docker.pkg.dev/${var.project_id}/reputation/reputation:latest"

      env {
        name  = "SERVICE"
        value = "beat"
      }
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name = "DATABASE_URL"
        value = "postgresql+asyncpg://${var.db_user}:${coalesce(var.db_password, random_password.db.result)}@/reputation?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
      }
      env {
        name = "SYNC_DATABASE_URL"
        value = "postgresql://${var.db_user}:${coalesce(var.db_password, random_password.db.result)}@/reputation?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.main.host}:6379/0"
      }

      resources {
        limits = {
          cpu    = var.beat_cpu
          memory = var.beat_memory
        }
        cpu_idle = false
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
  }

  depends_on = [
    google_project_service.services,
  ]
}
