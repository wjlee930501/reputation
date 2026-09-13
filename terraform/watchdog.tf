# ═══════════════════════════════════════════════════════════════════
# Re:putation — 파이프라인 외부 감시 (Cloud Scheduler)
# ═══════════════════════════════════════════════════════════════════
#
# Beat나 Worker가 죽으면 23:00 생성과 08:00 발행이 조용히 멈춘다. 그 사실을 알릴
# 작업 자체도 같은 Beat 위에 있으므로, 감시는 Celery 밖에 있어야 한다. Cloud
# Scheduler가 API(HTTP)를 직접 깨우고 API가 Redis·DB만 읽어 판정한다
# (backend/app/services/pipeline_watchdog.py).
#
# API Cloud Run 서비스는 ingress가 내부 LB 전용이므로 스케줄러는 공개 HTTPS LB
# (https://<domain>/api/v1/...)로 호출한다. 인증은 감시 전용 토큰 헤더 하나다 —
# admin 키를 스케줄러에 넣지 않기 위해 읽기 점검·알림 전송만 가능한 자격 증명을 쓴다.

resource "google_project_service" "cloudscheduler" {
  project            = var.project_id
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

resource "google_secret_manager_secret" "pipeline_watchdog_token" {
  secret_id = "PIPELINE_WATCHDOG_TOKEN"
  project   = var.project_id
  replication {
    auto {}
  }
}

# Cloud Run 주입과 app SA 접근 권한은 secretmanager.tf의 local.app_secret_env가 담당한다
# (다른 secret과 같은 경로). scripts/deploy.sh도 backend 필수 시크릿 목록에 넣어 두므로
# 두 배포 경로 모두 같은 값을 API에 주입한다.

# 스케줄러 헤더에 넣을 실제 값. 버전은 사람이 먼저 넣어 둔다
# (scripts/setup-gcp.sh가 빈 secret 컨테이너를 만든다).
data "google_secret_manager_secret_version" "pipeline_watchdog_token" {
  project = var.project_id
  secret  = google_secret_manager_secret.pipeline_watchdog_token.secret_id
  version = "latest"
}

locals {
  watchdog_alert_url     = "https://${var.domain}/api/v1/admin/watchdog/pipeline/alert"
  watchdog_token_headers = {
    "X-Watchdog-Token" = data.google_secret_manager_secret_version.pipeline_watchdog_token.secret_data
    "Content-Type"     = "application/json"
  }
}

# 5분 주기 하트비트 — 큐 canary 신선도와 예약 실행기 생존을 본다. 정상이면 아무것도
# 보내지 않고, 같은 원인은 KST 시간당 한 건으로 묶인다(서버측 중복 억제).
resource "google_cloud_scheduler_job" "watchdog_heartbeat" {
  name        = "${var.app_name}-watchdog-heartbeat"
  project     = var.project_id
  region      = var.region
  description = "자동 운영 파이프라인 외부 감시 — 예약 실행기·대기열 생존 확인 (5분)"
  schedule    = "*/5 * * * *"
  time_zone   = "Asia/Seoul"

  # 한 번 놓쳐도 5분 뒤 다음 실행이 같은 사실을 다시 판정한다. 재시도는 짧게 둔다.
  attempt_deadline = "60s"

  retry_config {
    retry_count          = 2
    min_backoff_duration = "10s"
    max_backoff_duration = "30s"
  }

  http_target {
    http_method = "POST"
    uri         = local.watchdog_alert_url
    headers     = local.watchdog_token_headers
  }

  depends_on = [google_project_service.cloudscheduler]
}

# 매일 08:30 KST — 아침 자동 발행(08:00)이 실제로 오늘 글을 공개했는지 확인한다.
# 하트비트와 같은 엔드포인트를 쓰므로 발행 확인만 하는 별도 코드 경로가 없다.
resource "google_cloud_scheduler_job" "watchdog_publish_check" {
  name        = "${var.app_name}-watchdog-publish-check"
  project     = var.project_id
  region      = var.region
  description = "자동 운영 파이프라인 외부 감시 — 당일 08:00 발행 결과 확인 (매일 08:30 KST)"
  schedule    = "30 8 * * *"
  time_zone   = "Asia/Seoul"

  attempt_deadline = "120s"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "30s"
    max_backoff_duration = "120s"
  }

  http_target {
    http_method = "POST"
    uri         = local.watchdog_alert_url
    headers     = local.watchdog_token_headers
  }

  depends_on = [google_project_service.cloudscheduler]
}
