# ═══════════════════════════════════════════════════════════════════
# Re:putation — Cloud Storage
# ═══════════════════════════════════════════════════════════════════

resource "google_storage_bucket" "images" {
  name          = "${var.app_name}-images-${var.project_id}"
  location      = var.images_bucket_location
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true

  # NO age-based delete rule here: published content images are referenced
  # forever by live hub pages (ContentItem.image_url) — expiring them would
  # 404 every article older than the cutoff.
}

resource "google_storage_bucket" "reports" {
  name          = "${var.app_name}-reports-${var.project_id}"
  location      = var.reports_bucket_location
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true
}
