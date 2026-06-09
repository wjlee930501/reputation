# ═══════════════════════════════════════════════════════════════════
# Re:putation — Secret Manager
# ═══════════════════════════════════════════════════════════════════

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "ANTHROPIC_API_KEY"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "openai_api_key" {
  secret_id = "OPENAI_API_KEY"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "GEMINI_API_KEY"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "slack_webhook_url" {
  secret_id = "SLACK_WEBHOOK_URL"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "admin_secret_key" {
  secret_id = "ADMIN_SECRET_KEY"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "admin_session_secret" {
  secret_id = "ADMIN_SESSION_SECRET"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "DB_PASSWORD"
  project   = var.project_id
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "site_revalidate_secret" {
  secret_id = "SITE_REVALIDATE_SECRET"
  project   = var.project_id
  replication {
    auto {}
  }
}

# CDX-M1: site(Vercel) BFF가 방문자 IP를 backend에 인증 전달할 때 쓰는 공유 secret.
# Vercel 프로젝트 환경변수 SITE_BFF_SECRET에 동일 값을 설정해야 한다.
resource "google_secret_manager_secret" "site_bff_secret" {
  secret_id = "SITE_BFF_SECRET"
  project   = var.project_id
  replication {
    auto {}
  }
}

# Secret Manager IAM — service account access
locals {
  app_secret_env = {
    ANTHROPIC_API_KEY      = google_secret_manager_secret.anthropic_api_key.secret_id
    OPENAI_API_KEY         = google_secret_manager_secret.openai_api_key.secret_id
    GEMINI_API_KEY         = google_secret_manager_secret.gemini_api_key.secret_id
    SLACK_WEBHOOK_URL      = google_secret_manager_secret.slack_webhook_url.secret_id
    ADMIN_SECRET_KEY       = google_secret_manager_secret.admin_secret_key.secret_id
    ADMIN_SESSION_SECRET   = google_secret_manager_secret.admin_session_secret.secret_id
    DB_PASSWORD            = google_secret_manager_secret.db_password.secret_id
    SITE_REVALIDATE_SECRET = google_secret_manager_secret.site_revalidate_secret.secret_id
    SITE_BFF_SECRET        = google_secret_manager_secret.site_bff_secret.secret_id
  }

  # 프론트엔드(Next.js) 서비스가 마운트하는 secret — admin BFF 세션/키, site
  # revalidate/BFF 인증. 백엔드 전용 secret(API 키·DB 비밀번호)에는 접근 불가.
  admin_secret_env = {
    ADMIN_SECRET_KEY     = google_secret_manager_secret.admin_secret_key.secret_id
    ADMIN_SESSION_SECRET = google_secret_manager_secret.admin_session_secret.secret_id
  }
  site_secret_env = {
    SITE_REVALIDATE_SECRET = google_secret_manager_secret.site_revalidate_secret.secret_id
    SITE_BFF_SECRET        = google_secret_manager_secret.site_bff_secret.secret_id
  }
}

# iam_member(비배타적)를 사용 — 같은 secret/role에 app SA와 frontend SA가 공존한다.
# (iam_binding은 role 단위 authoritative라 두 SA를 한 리소스에서 관리해야 해서 부적합.)
resource "google_secret_manager_secret_iam_member" "app_access" {
  for_each  = local.app_secret_env
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_secret_manager_secret_iam_member" "frontend_access" {
  for_each  = merge(local.admin_secret_env, local.site_secret_env)
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.frontend.email}"
}
