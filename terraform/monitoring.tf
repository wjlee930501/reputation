# ═══════════════════════════════════════════════════════════════════
# Re:putation — Uptime Monitoring + Alerting
#
# 외부 업타임 체크가 LB를 통해 API(/api/v1/health/live)와 site(/)를 감시하고,
# 실패 시 이메일로 알림한다. var.alert_email은 필수(빈 값 거부) — 운영 의도상
# "무알림 배포"를 방지하기 위해 알림 채널 없이 인프라가 뜨는 것을 막는다.
# ═══════════════════════════════════════════════════════════════════

variable "alert_email" {
  description = <<-EOT
    Uptime/장애 알림을 받을 이메일 (필수). 운영 의도상 알림 없는 배포를 막기 위해
    빈 값을 거부한다 — 이 값이 있어야 uptime 체크/알림 정책이 항상 생성된다.
  EOT
  type = string

  validation {
    condition     = length(trimspace(var.alert_email)) > 0 && can(regex("@", var.alert_email))
    error_message = "alert_email은 필수입니다 (무알림 배포 방지). 유효한 이메일 주소를 설정하세요."
  }
}

variable "notification_channels" {
  description = <<-EOT
    Monitoring notification channel IDs (full resource names, e.g.
    "projects/<project>/notificationChannels/<id>") for the worker/beat
    ERROR-log alert. Empty (default) skips creating that alert policy.
  EOT
  type        = list(string)
  default     = []
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "Re:putation ops email"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_uptime_check_config" "api" {
  count        = var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "${var.app_name}-api-uptime"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/api/v1/health/live"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.domain
    }
  }
}

resource "google_monitoring_uptime_check_config" "site" {
  count        = var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "${var.app_name}-site-uptime"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.domain
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  count        = var.alert_email != "" ? 1 : 0
  project      = var.project_id
  display_name = "${var.app_name} uptime failure"
  combiner     = "OR"

  conditions {
    display_name = "API uptime check failing"
    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.api[0].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }

  conditions {
    display_name = "Site uptime check failing"
    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.site[0].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]

  documentation {
    content = "Re:putation 공개 표면 업타임 실패. 런북: docs/plans/2026-06-09-gcp-full-deployment-runbook.md"
  }
}

# ── Worker/Beat ERROR-log alert (log-based) ────────────────────────
# Celery worker/beat 컨테이너의 ERROR 이상 로그를 알림으로 승격한다 —
# 콘텐츠 생성·SoV 측정·리포트 태스크 실패는 사용자에게 즉시 보이지 않으므로
# 로그 기반 알림이 없으면 조용히 누적된다. var.notification_channels가
# 비어 있으면 생성하지 않는다.
resource "google_monitoring_alert_policy" "worker_beat_errors" {
  count        = length(var.notification_channels) > 0 ? 1 : 0
  project      = var.project_id
  display_name = "${var.app_name} worker/beat ERROR logs"
  combiner     = "OR"

  conditions {
    display_name = "ERROR log entries from worker/beat"
    condition_matched_log {
      filter = "resource.type=\"cloud_run_revision\" AND severity>=ERROR AND (resource.labels.service_name=\"${var.app_name}-worker\" OR resource.labels.service_name=\"${var.app_name}-beat\")"
    }
  }

  # condition_matched_log 정책은 notification_rate_limit이 필수.
  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
    auto_close = "86400s"
  }

  notification_channels = var.notification_channels

  documentation {
    content = "Re:putation worker/beat에서 ERROR 로그 발생. Cloud Logging에서 해당 서비스 로그를 확인할 것."
  }
}
