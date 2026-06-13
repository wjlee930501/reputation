# ═══════════════════════════════════════════════════════════════════
# Re:putation — Cloud Run Services
# ═══════════════════════════════════════════════════════════════════

locals {
  # INFRA-2: Pin the deployed image. Prefer an immutable digest passed via
  # var.api_image (set by CI/CD). Fall back to a registry tag only if unset.
  # NOTE: the fallback uses a tag — set var.api_image to an @sha256:... digest
  # in production for reproducible deploys/rollbacks.
  app_image = var.api_image != "" ? var.api_image : "${var.region}-docker.pkg.dev/${var.project_id}/reputation/reputation:latest"

  # AUTH-5: Effective browser CORS allowlist. If the operator does not override
  # var.allowed_origins, derive HTTPS origins from the configured domains.
  default_allowed_origins = compact([
    "https://${var.domain}",
    var.admin_subdomain != "" ? "https://${var.admin_subdomain}" : "",
  ])
  effective_allowed_origins = length(var.allowed_origins) > 0 ? var.allowed_origins : local.default_allowed_origins
}

# ── API Service ────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "api" {
  name     = "${var.app_name}-api"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.app.email

    containers {
      image = local.app_image

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
        name  = "DB_USER"
        value = var.db_user
      }
      env {
        name  = "DB_NAME"
        value = var.db_name
      }
      env {
        name  = "CLOUD_SQL_CONNECTION_NAME"
        value = google_sql_database_instance.main.connection_name
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
      env {
        name  = "SITE_REVALIDATE_URL"
        value = var.site_revalidate_url != "" ? var.site_revalidate_url : "https://${var.domain}/api/revalidate"
      }
      # Slack 알림의 admin 링크 / llms.txt 절대 URL — 미설정 시 localhost 기본값이
      # 운영 알림에 새어 나간다.
      env {
        name  = "ADMIN_BASE_URL"
        value = var.admin_subdomain != "" ? "https://${var.admin_subdomain}" : "https://${var.domain}"
      }
      env {
        name  = "SITE_BASE_URL"
        value = "https://${var.domain}"
      }
      env {
        name  = "CNAME_TARGET"
        value = var.cname_target
      }
      env {
        name  = "CUSTOM_DOMAIN_IP_TARGETS"
        value = google_compute_global_address.lb_ip.address
      }
      # AUTH-1: Trust the load-balancer hop so rate limits / consent IPs key on
      # the real client (X-Forwarded-For) instead of the Google front-end IP.
      env {
        name  = "TRUSTED_PROXY_IPS"
        value = join(",", var.trusted_proxy_ips)
      }
      # AUTH-5: Browser CORS allowlist (credentials are allowed → no wildcard,
      # no localhost in production). Backend boot fails if this is misconfigured.
      env {
        name  = "ALLOWED_ORIGINS"
        value = join(",", local.effective_allowed_origins)
      }
      # OBS-1: Structured JSON logging at INFO so Cloud Logging can parse severity.
      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }
      env {
        name  = "LOG_JSON"
        value = "true"
      }
      env {
        name  = "PUBLIC_SITE_RATE_LIMIT"
        value = var.public_site_rate_limit
      }
      # Optional Sentry error tracking — env only rendered when var.sentry_dsn set.
      dynamic "env" {
        for_each = var.sentry_dsn != "" ? [var.sentry_dsn] : []
        content {
          name  = "SENTRY_DSN"
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.app_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
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

  # Ownership split: terraform manages the infra shape (env, scaling, probes,
  # Cloud SQL volume, SA), while scripts/deploy.sh owns image rollouts via
  # `gcloud run deploy`. Without this, a later `terraform apply` would revert
  # the running revision to var.api_image (or the :latest fallback nothing
  # pushes). var.api_image is still used for INITIAL creation.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_iam_member.app_access,
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
      image = local.app_image

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
        name  = "DB_USER"
        value = var.db_user
      }
      env {
        name  = "DB_NAME"
        value = var.db_name
      }
      env {
        name  = "CLOUD_SQL_CONNECTION_NAME"
        value = google_sql_database_instance.main.connection_name
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
      env {
        name  = "SITE_REVALIDATE_URL"
        value = var.site_revalidate_url != "" ? var.site_revalidate_url : "https://${var.domain}/api/revalidate"
      }
      env {
        name  = "ADMIN_BASE_URL"
        value = var.admin_subdomain != "" ? "https://${var.admin_subdomain}" : "https://${var.domain}"
      }
      env {
        name  = "SITE_BASE_URL"
        value = "https://${var.domain}"
      }
      env {
        name  = "CNAME_TARGET"
        value = var.cname_target
      }
      env {
        name  = "CUSTOM_DOMAIN_IP_TARGETS"
        value = google_compute_global_address.lb_ip.address
      }
      env {
        name  = "TRUSTED_PROXY_IPS"
        value = join(",", var.trusted_proxy_ips)
      }
      env {
        name  = "ALLOWED_ORIGINS"
        value = join(",", local.effective_allowed_origins)
      }
      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }
      env {
        name  = "LOG_JSON"
        value = "true"
      }
      # Optional Sentry error tracking — env only rendered when var.sentry_dsn set.
      dynamic "env" {
        for_each = var.sentry_dsn != "" ? [var.sentry_dsn] : []
        content {
          name  = "SENTRY_DSN"
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.app_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      resources {
        limits = {
          cpu    = var.worker_cpu
          memory = var.worker_memory
        }
        cpu_idle = false
      }

      # entrypoint가 $PORT에 헬스 서버를 띄운다 (Cloud Run 서비스 필수 요건).
      ports {
        container_port = 8080
      }

      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 6
        tcp_socket {
          port = 8080
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
      min_instance_count = var.worker_min_instances
      max_instance_count = var.worker_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
  }

  # deploy.sh owns image rollouts (see api service comment above).
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_iam_member.app_access,
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
      image = local.app_image

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
        name  = "DB_USER"
        value = var.db_user
      }
      env {
        name  = "DB_NAME"
        value = var.db_name
      }
      env {
        name  = "CLOUD_SQL_CONNECTION_NAME"
        value = google_sql_database_instance.main.connection_name
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.main.host}:6379/0"
      }
      env {
        name  = "CNAME_TARGET"
        value = var.cname_target
      }
      env {
        name  = "CUSTOM_DOMAIN_IP_TARGETS"
        value = google_compute_global_address.lb_ip.address
      }
      env {
        name  = "ADMIN_BASE_URL"
        value = var.admin_subdomain != "" ? "https://${var.admin_subdomain}" : "https://${var.domain}"
      }
      env {
        name  = "SITE_BASE_URL"
        value = "https://${var.domain}"
      }
      env {
        name  = "TRUSTED_PROXY_IPS"
        value = join(",", var.trusted_proxy_ips)
      }
      env {
        name  = "ALLOWED_ORIGINS"
        value = join(",", local.effective_allowed_origins)
      }
      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }
      env {
        name  = "LOG_JSON"
        value = "true"
      }
      # Optional Sentry error tracking — env only rendered when var.sentry_dsn set.
      dynamic "env" {
        for_each = var.sentry_dsn != "" ? [var.sentry_dsn] : []
        content {
          name  = "SENTRY_DSN"
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.app_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      resources {
        limits = {
          cpu    = var.beat_cpu
          memory = var.beat_memory
        }
        cpu_idle = false
      }

      # entrypoint가 $PORT에 헬스 서버를 띄운다 (Cloud Run 서비스 필수 요건).
      ports {
        container_port = 8080
      }

      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 6
        tcp_socket {
          port = 8080
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
      min_instance_count = var.beat_min_instances
      max_instance_count = var.beat_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
  }

  # deploy.sh owns image rollouts (see api service comment above).
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_iam_member.app_access,
  ]
}

# ── Migration Job (alembic upgrade head) ──────────────────────────
# Cloud Run Job mirror of the api service: same SA, secrets, env, and — critically —
# the Cloud SQL volume/instance attachment. Production DATABASE_URL is built from
# DB_* parts as a /cloudsql/<connection_name> unix socket (config.py), so without
# the cloud_sql_instance attachment the migration cannot reach the database.
# deploy.sh `migrate`/`all` executes (and image-updates) this job before rolling
# out api/worker/beat.
resource "google_cloud_run_v2_job" "migrate" {
  name     = "${var.app_name}-migrate"
  location = var.region
  project  = var.project_id

  template {
    template {
      service_account = google_service_account.app.email

      containers {
        image = local.app_image

        env {
          name  = "SERVICE"
          value = "migrate"
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
          name  = "DB_USER"
          value = var.db_user
        }
        env {
          name  = "DB_NAME"
          value = var.db_name
        }
        env {
          name  = "CLOUD_SQL_CONNECTION_NAME"
          value = google_sql_database_instance.main.connection_name
        }
        env {
          name  = "REDIS_URL"
          value = "redis://${google_redis_instance.main.host}:6379/0"
        }
        env {
          name  = "CNAME_TARGET"
          value = var.cname_target
        }
        env {
          name  = "CUSTOM_DOMAIN_IP_TARGETS"
          value = google_compute_global_address.lb_ip.address
        }
        # config.py validates these at boot in production (AUTH-1/AUTH-5) — the
        # migration container imports settings, so they must be present here too.
        env {
          name  = "ADMIN_BASE_URL"
          value = var.admin_subdomain != "" ? "https://${var.admin_subdomain}" : "https://${var.domain}"
        }
        env {
          name  = "SITE_BASE_URL"
          value = "https://${var.domain}"
        }
        env {
          name  = "TRUSTED_PROXY_IPS"
          value = join(",", var.trusted_proxy_ips)
        }
        env {
          name  = "ALLOWED_ORIGINS"
          value = join(",", local.effective_allowed_origins)
        }
        dynamic "env" {
          for_each = local.app_secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
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

      timeout     = "300s"
      max_retries = 1

      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }
  }

  # deploy.sh owns image rollouts (see api service comment above).
  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_iam_member.app_access,
  ]
}
