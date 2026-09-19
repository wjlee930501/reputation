# ═══════════════════════════════════════════════════════════════════
# Re:putation — Terraform Main
# ═══════════════════════════════════════════════════════════════════

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source = "hashicorp/google"
      # 메이저를 고정한다. 상한 없는 ">= 5.0"이면 새 클론이 다음 메이저를 받아
      # google_sql_database_instance 등에서 destroy/create 계획을 낼 수 있다.
      # 실제 버전은 terraform/.terraform.lock.hcl(추적됨)이 고정한다.
      version = "~> 7.33"
    }
  }

  backend "gcs" {
    # Configure via terraform init -backend-config:
    #   terraform init -backend-config="bucket=${PROJECT_ID}-tfstate"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Enable required APIs
resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "secretmanager.googleapis.com",
    "compute.googleapis.com",
    "certificatemanager.googleapis.com", # 하이브리드 도메인 cert 평면 (certmanager.tf)
    "servicenetworking.googleapis.com",
    "vpcaccess.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iamcredentials.googleapis.com",
    "monitoring.googleapis.com",
    "dns.googleapis.com",
    # 새 경로는 Vertex를 직접 부르지 않지만, 구 리비전의 이미지 생성이 아직 부른다.
    # 롤백 경로가 살아 있어야 하므로 전환이 끝날 때까지 목록에 남긴다.
    "aiplatform.googleapis.com",
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

data "google_project" "project" {
  project_id = var.project_id
}
