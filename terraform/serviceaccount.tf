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

# Signed URL 생성: Cloud Run에는 SA 키 파일이 없어 generate_signed_url이
# IAM signBlob API로 서명한다 — SA가 자기 자신을 서명자로 쓸 권한이 필요하다.
# 없으면 공개 콘텐츠 이미지/자산 서빙(302 signed URL)과 리포트 다운로드가 전부 실패.
resource "google_service_account_iam_member" "app_self_token_creator" {
  service_account_id = google_service_account.app.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.app.email}"
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
