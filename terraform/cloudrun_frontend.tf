# ═══════════════════════════════════════════════════════════════════
# Re:putation — Frontend Cloud Run Services (Next.js site + admin)
#
# 전체 GCP 아키텍처:
#   User → HTTPS LB
#     host <domain>           → site 서비스 (path /api/v1/* 만 API 백엔드)
#     host <admin_subdomain>  → admin 서비스
#
# 프론트엔드 SA는 백엔드 app SA와 분리 — DB/Redis/Vertex 권한 없이
# 자기 secret 4개의 accessor + 로그 기록만 가진다 (최소 권한).
# ═══════════════════════════════════════════════════════════════════

locals {
  site_image  = var.site_image != "" ? var.site_image : "${var.region}-docker.pkg.dev/${var.project_id}/reputation/site:latest"
  admin_image = var.admin_image != "" ? var.admin_image : "${var.region}-docker.pkg.dev/${var.project_id}/reputation/admin:latest"

  public_origin = "https://${var.domain}"
}

# ── Frontend Service Account ───────────────────────────────────────
resource "google_service_account" "frontend" {
  account_id   = "${var.app_name}-frontend-sa"
  display_name = "Re:putation Frontend (Next.js) Service Account"
  project      = var.project_id
}

resource "google_project_iam_member" "frontend_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.frontend.email}"

  depends_on = [google_project_service.services]
}

# ── Public Site (Next.js SSG/ISR) ──────────────────────────────────
resource "google_cloud_run_v2_service" "site" {
  name     = "${var.app_name}-site"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.frontend.email

    containers {
      image = local.site_image

      # 서버사이드(SSG/ISR/route handler) 런타임 env. 클라이언트 번들 쪽 값은
      # 이미지 빌드 시 build-arg로 인라인된다 — 두 값을 일치시킬 것.
      env {
        name  = "NEXT_PUBLIC_API_URL"
        value = "${local.public_origin}/api/v1/public"
      }
      env {
        name  = "NEXT_PUBLIC_SITE_URL"
        value = local.public_origin
      }
      env {
        name  = "NEXT_PUBLIC_BACKEND_URL"
        value = local.public_origin
      }
      env {
        name  = "BACKEND_URL"
        value = local.public_origin
      }
      dynamic "env" {
        for_each = local.site_secret_env
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
          cpu    = var.site_cpu
          memory = var.site_memory
        }
        startup_cpu_boost = true
      }

      ports {
        container_port = 8080
      }

      # HTTP probe 대신 TCP — 첫 ISR 렌더가 API에 의존하므로 API 장애가
      # 컨테이너 기동 실패로 번지지 않게 한다.
      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 6
        tcp_socket {
          port = 8080
        }
      }
    }

    scaling {
      min_instance_count = var.site_min_instances
      max_instance_count = var.site_max_instances
    }
  }

  # scripts/deploy.sh owns image rollouts (gcloud run deploy); terraform manages
  # the rest of the service shape. Prevents `terraform apply` reverting the
  # running revision to var.site_image / the :latest fallback nothing pushes.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_iam_member.frontend_access,
  ]
}

# ── Admin Console (Next.js BFF) ────────────────────────────────────
resource "google_cloud_run_v2_service" "admin" {
  name     = "${var.app_name}-admin"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.frontend.email

    containers {
      image = local.admin_image

      # admin BFF → backend API 호출은 공개 LB(https://<domain>/api/v1/admin/*)를
      # 경유한다. X-Admin-Key 헤더 인증은 BFF가 Secret Manager 값으로 부착.
      env {
        name  = "BACKEND_URL"
        value = local.public_origin
      }
      env {
        name  = "NEXT_PUBLIC_BACKEND_URL"
        value = local.public_origin
      }
      dynamic "env" {
        for_each = local.admin_secret_env
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
          cpu    = var.admin_cpu
          memory = var.admin_memory
        }
        startup_cpu_boost = true
      }

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
    }

    scaling {
      min_instance_count = var.admin_min_instances
      max_instance_count = var.admin_max_instances
    }
  }

  # scripts/deploy.sh owns image rollouts (see site service comment above).
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_iam_member.frontend_access,
  ]
}

# ── Unauthenticated invoker (LB 경유 트래픽 허용) ──────────────────
# Serverless NEG는 IAM 인증을 대신하지 않는다 — run.invoker(allUsers)가 없으면
# LB를 거친 요청도 403. ingress=INTERNAL_LOAD_BALANCER가 직접 접근을 차단하므로
# allUsers 부여는 "LB를 통과한 모든 요청 허용"을 의미한다.
# (조직 정책 constraints/iam.allowedPolicyMemberDomains가 allUsers를 막는
# 환경이면 LB에 IAP를 붙이는 방식으로 대체해야 한다 — 런북 참고.)
resource "google_cloud_run_v2_service_iam_member" "api_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "site_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.site.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "admin_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.admin.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
