# ═══════════════════════════════════════════════════════════════════
# Re:putation — Secret Manager
# ═══════════════════════════════════════════════════════════════════

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "ANTHROPIC_API_KEY"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "anthropic_api_key" {
  secret      = google_secret_manager_secret.anthropic_api_key.id
  secret_data = var.anthropic_api_key
}

resource "google_secret_manager_secret" "openai_api_key" {
  secret_id = "OPENAI_API_KEY"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "openai_api_key" {
  secret      = google_secret_manager_secret.openai_api_key.id
  secret_data = var.openai_api_key
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "GEMINI_API_KEY"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "gemini_api_key" {
  secret      = google_secret_manager_secret.gemini_api_key.id
  secret_data = var.gemini_api_key
}

resource "google_secret_manager_secret" "slack_webhook_url" {
  secret_id = "SLACK_WEBHOOK_URL"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "slack_webhook_url" {
  secret      = google_secret_manager_secret.slack_webhook_url.id
  secret_data = var.slack_webhook_url
}

resource "google_secret_manager_secret" "admin_secret_key" {
  secret_id = "ADMIN_SECRET_KEY"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "admin_secret_key" {
  secret      = google_secret_manager_secret.admin_secret_key.id
  secret_data = var.admin_secret_key
}

resource "google_secret_manager_secret" "admin_login_password" {
  secret_id = "ADMIN_LOGIN_PASSWORD"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "admin_login_password" {
  secret      = google_secret_manager_secret.admin_login_password.id
  secret_data = var.admin_login_password
}

resource "google_secret_manager_secret" "admin_session_secret" {
  secret_id = "ADMIN_SESSION_SECRET"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "admin_session_secret" {
  secret      = google_secret_manager_secret.admin_session_secret.id
  secret_data = var.admin_session_secret
}

resource "google_secret_manager_secret" "site_revalidate_secret" {
  count     = var.site_revalidate_secret != "" ? 1 : 0
  secret_id = "SITE_REVALIDATE_SECRET"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "site_revalidate_secret" {
  count       = var.site_revalidate_secret != "" ? 1 : 0
  secret      = google_secret_manager_secret.site_revalidate_secret[0].id
  secret_data = var.site_revalidate_secret
}

# Secret Manager IAM — service account access
resource "google_secret_manager_secret_iam_binding" "app_access" {
  for_each = {
    ANTHROPIC_API_KEY      = google_secret_manager_secret.anthropic_api_key.secret_id
    OPENAI_API_KEY         = google_secret_manager_secret.openai_api_key.secret_id
    GEMINI_API_KEY         = google_secret_manager_secret.gemini_api_key.secret_id
    SLACK_WEBHOOK_URL      = google_secret_manager_secret.slack_webhook_url.secret_id
    ADMIN_SECRET_KEY       = google_secret_manager_secret.admin_secret_key.secret_id
    ADMIN_LOGIN_PASSWORD   = google_secret_manager_secret.admin_login_password.secret_id
    ADMIN_SESSION_SECRET   = google_secret_manager_secret.admin_session_secret.secret_id
  }
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  members   = ["serviceAccount:${google_service_account.app.email}"]
}
