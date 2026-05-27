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
  }
}

resource "google_secret_manager_secret_iam_binding" "app_access" {
  for_each  = local.app_secret_env
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  members   = ["serviceAccount:${google_service_account.app.email}"]
}
