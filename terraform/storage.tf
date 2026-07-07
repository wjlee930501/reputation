# ═══════════════════════════════════════════════════════════════════
# Re:putation — Cloud Storage
# ═══════════════════════════════════════════════════════════════════

resource "google_storage_bucket" "images" {
  name          = "${var.app_name}-images-${var.project_id}"
  location      = var.images_bucket_location
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true

  # 실수로 덮어쓰거나 삭제해도 복구할 수 있도록 object versioning을 켠다.
  versioning {
    enabled = true
  }

  # NO age-based delete on LIVE objects: published content images are referenced
  # forever by live hub pages (ContentItem.image_url) — expiring the current
  # version would 404 every article older than the cutoff. 아래 규칙은 NONCURRENT
  # (덮어써져 archived된 구버전)만 정리해 versioning으로 인한 스토리지 누수를 막는다.
  lifecycle_rule {
    condition {
      with_state         = "ARCHIVED"
      num_newer_versions = 3
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      with_state                 = "ARCHIVED"
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "reports" {
  name          = "${var.app_name}-reports-${var.project_id}"
  location      = var.reports_bucket_location
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true

  # V0/월간 PDF 리포트도 실수 덮어쓰기 복구를 위해 versioning + 구버전 정리.
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      with_state         = "ARCHIVED"
      num_newer_versions = 3
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      with_state                 = "ARCHIVED"
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }
}
