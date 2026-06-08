# ═══════════════════════════════════════════════════════════════════
# Re:putation — Service Account + IAM
# ═══════════════════════════════════════════════════════════════════

resource "google_service_account" "app" {
  account_id   = "${var.app_name}-sa"
  display_name = "Re:putation Application Service Account"
  project      = var.project_id
}

# Wait for APIs before assigning roles
#
# INFRA-7 (least-privilege intent):
#   - roles/cloudsql.client      — required: Cloud SQL connector socket.
#   - roles/aiplatform.user      — OVER-BROAD for our use. The app only calls
#       Vertex AI Imagen 3 prediction (aiplatform.endpoints.predict). This role
#       grants project-wide Vertex access. Preferred follow-up: replace with a
#       custom role (var.aiplatform_role) carrying only the predict permission,
#       and/or split a dedicated worker SA so the internet-adjacent API SA does
#       not carry AI access. Kept as a variable default to avoid breaking image
#       generation; override var.aiplatform_role with a custom role to tighten.
#   - roles/logging.logWriter    — required: structured logs to Cloud Logging.
#   - roles/monitoring.metricWriter, roles/cloudtrace.agent,
#     roles/errorreporting.writer — required: observability signals.
# Storage (per-bucket) and Secret Manager (per-secret) bindings are already
# scoped narrowly below / in secretmanager.tf.
resource "google_project_iam_member" "roles" {
  for_each = toset([
    "roles/cloudsql.client",
    var.aiplatform_role,
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
