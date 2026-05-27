# ═══════════════════════════════════════════════════════════════════
# Re:putation — VPC + Serverless VPC Access
# 
# Cloud Run은 Serverless VPC Access Connector를 통해
# Memorystore Redis와 Cloud SQL (private IP)에 연결.
# ═══════════════════════════════════════════════════════════════════

resource "google_compute_network" "vpc" {
  name                    = "${var.app_name}-vpc"
  auto_create_subnetworks = false
  project                 = var.project_id
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.app_name}-subnet"
  network       = google_compute_network.vpc.id
  region        = var.region
  ip_cidr_range = "10.0.0.0/24"
  project       = var.project_id
}

# Serverless VPC Access — Cloud Run → VPC 내부 리소스 연결
resource "google_vpc_access_connector" "connector" {
  name          = "${var.app_name}-vpc-connector"
  region        = var.region
  project       = var.project_id
  network       = google_compute_network.vpc.name
  ip_cidr_range = "10.8.0.0/28"
  min_instances = 2
  max_instances = 10

  depends_on = [google_project_service.services]
}

# Private services access — Cloud SQL private IP용
resource "google_compute_global_address" "private_ip_range" {
  name          = "${var.app_name}-private-ip-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
  project       = var.project_id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]

  depends_on = [google_project_service.services]
}
