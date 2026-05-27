# ═══════════════════════════════════════════════════════════════════
# Re:putation — Cloud Storage
# ═══════════════════════════════════════════════════════════════════

resource "google_storage_bucket" "images" {
  name          = "${var.app_name}-images-${var.project_id}"
  location      = var.images_bucket_location
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition { age = 365 }
    action { type = "Delete" }
  }
}

resource "google_storage_bucket" "reports" {
  name          = "${var.app_name}-reports-${var.project_id}"
  location      = var.reports_bucket_location
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true
}

# Public access for images (CDN/caching via LB)
resource "google_storage_bucket_iam_member" "images_public" {
  bucket = google_storage_bucket.images.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
