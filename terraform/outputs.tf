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

output "redis_auth_string" {
  description = "Memorystore Redis AUTH string (INFRA-5). Use in REDIS_URL as rediss://:<auth>@<host>:<port>/0."
  value       = google_redis_instance.main.auth_string
  sensitive   = true
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

output "site_service_name" {
  description = "Cloud Run public site (Next.js) service name"
  value       = google_cloud_run_v2_service.site.name
}

output "admin_service_name" {
  description = "Cloud Run admin console (Next.js) service name"
  value       = google_cloud_run_v2_service.admin.name
}

output "site_url" {
  description = "Public site entrypoint through the HTTPS load balancer"
  value       = "https://${var.domain}"
}

output "admin_url" {
  description = "Admin console entrypoint (empty if admin_subdomain unset)"
  value       = var.admin_subdomain != "" ? "https://${var.admin_subdomain}" : ""
}

output "frontend_service_account_email" {
  description = "Frontend (Next.js) service account email"
  value       = google_service_account.frontend.email
}
