# ═══════════════════════════════════════════════════════════════════
# Re:putation — Uptime Monitoring + Alerting
#
# var.alert_email이 설정된 경우에만 생성. 외부 업타임 체크가 LB를 통해
# API(/api/v1/health/live)와 site(/)를 감시하고, 실패 시 이메일 알림.
# ═══════════════════════════════════════════════════════════════════

variable "alert_email" {
  description = "Uptime/장애 알림을 받을 이메일. 빈 값이면 모니터링 리소스를 만들지 않는다."
  type        = string
  default     = ""
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
