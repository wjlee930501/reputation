# ═══════════════════════════════════════════════════════════════════
# Re:putation — Terraform Outputs
# ═══════════════════════════════════════════════════════════════════

output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "api_service_url" {
  description = "Public API entrypoint through the HTTPS load balancer"
  value       = "https://${var.domain}"
}

output "worker_service_name" {
  description = "Cloud Run Worker service name"
  value       = google_cloud_run_v2_service.worker.name
}

output "beat_service_name" {
  description = "Cloud Run Beat service name"
  value       = google_cloud_run_v2_service.beat.name
}

output "database_connection_name" {
  description = "Cloud SQL instance connection name for /cloudsql socket"
  value       = google_sql_database_instance.main.connection_name
}

output "database_host" {
  description = "Cloud SQL private IP"
  value       = google_sql_database_instance.main.private_ip_address
}

output "redis_host" {
  description = "Memorystore Redis host IP"
  value       = google_redis_instance.main.host
}

output "redis_port" {
  description = "Memorystore Redis port"
  value       = google_redis_instance.main.port
}

output "images_bucket" {
  value = google_storage_bucket.images.name
}

output "reports_bucket" {
  value = google_storage_bucket.reports.name
}

output "service_account_email" {
  description = "Application service account email"
  value       = google_service_account.app.email
}

output "vpc_connector_id" {
  description = "Serverless VPC Access connector (for Cloud Run config)"
  value       = google_vpc_access_connector.connector.id
}
