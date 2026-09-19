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
#   - roles/logging.logWriter    — required: structured logs to Cloud Logging.
#   - roles/monitoring.metricWriter, roles/cloudtrace.agent,
#     roles/errorreporting.writer — required: observability signals.
# 모든 LLM·이미지 생성 호출은 OpenRouter API로 나가므로 Vertex AI 권한은 두지 않는다.
# Storage (per-bucket) and Secret Manager (per-secret) bindings are already
# scoped narrowly below / in secretmanager.tf.
resource "google_project_iam_member" "roles" {
  for_each = toset([
    "roles/cloudsql.client",
    var.certificate_manager_role,
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
