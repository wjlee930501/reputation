# ═══════════════════════════════════════════════════════════════════
# Re:putation — Service Account + IAM
# ═══════════════════════════════════════════════════════════════════

resource "google_service_account" "app" {
  account_id   = "${var.app_name}-sa"
  display_name = "Re:putation Application Service Account"
  project      = var.project_id
}

# Wait for APIs before assigning roles
resource "google_project_iam_member" "roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/errorreporting.writer",
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.app.email}"

  depends_on = [google_project_service.services]
}

resource "google_storage_bucket_iam_member" "app_images_admin" {
  bucket = google_storage_bucket.images.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}

resource "google_storage_bucket_iam_member" "app_reports_admin" {
  bucket = google_storage_bucket.reports.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}
