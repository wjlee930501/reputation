# ═══════════════════════════════════════════════════════════════════
# Re:putation — HTTPS Load Balancer (전체 GCP 아키텍처)
#
#   User → HTTPS LB (managed SSL cert: domain + admin_subdomain)
#       host <domain>:
#         /api/v1/*  → Serverless NEG → Cloud Run API (public API + 자산)
#         그 외       → Serverless NEG → Cloud Run site (Next.js — /api/leads,
#                       /api/revalidate 같은 Next route handler 포함)
#       host <admin_subdomain>:
#         전부        → Serverless NEG → Cloud Run admin (Next.js BFF)
# ═══════════════════════════════════════════════════════════════════

# ── Serverless NEG (Cloud Run 연결) ────────────────────────────────
resource "google_compute_region_network_endpoint_group" "api_neg" {
  name                  = "${var.app_name}-api-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.api.name
  }
}

resource "google_compute_region_network_endpoint_group" "site_neg" {
  name                  = "${var.app_name}-site-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.site.name
  }
}

resource "google_compute_region_network_endpoint_group" "admin_neg" {
  count                 = var.admin_subdomain != "" ? 1 : 0
  name                  = "${var.app_name}-admin-neg"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.admin.name
  }
}

# ── Managed SSL Certificate ────────────────────────────────────────
locals {
  cert_domains = compact([
    var.domain,
    var.admin_subdomain != "" ? var.admin_subdomain : "",
  ])
}

resource "google_compute_managed_ssl_certificate" "main" {
  # 도메인 셋이 바뀌면 새 이름으로 cert를 먼저 만들고(provisioning 후 proxy 교체)
  # 이전 cert를 제거 — 이름 충돌 없이 create_before_destroy가 동작한다.
  name    = "${var.app_name}-cert-${substr(md5(join(",", local.cert_domains)), 0, 8)}"
  project = var.project_id

  managed {
    domains = local.cert_domains
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ── Reserved Global IP ─────────────────────────────────────────────
resource "google_compute_global_address" "lb_ip" {
  name    = "${var.app_name}-lb-ip"
  project = var.project_id
}

# ── Backend Services ───────────────────────────────────────────────
resource "google_compute_backend_service" "api" {
  name      = "${var.app_name}-api-backend"
  project   = var.project_id
  protocol  = "HTTP"
  port_name = "http"

  backend {
    group = google_compute_region_network_endpoint_group.api_neg.id
  }

  enable_cdn      = false
  security_policy = google_compute_security_policy.main.self_link

  log_config {
    enable      = true
    sample_rate = 0.1
  }
}

resource "google_compute_backend_service" "site" {
  name      = "${var.app_name}-site-backend"
  project   = var.project_id
  protocol  = "HTTP"
  port_name = "http"

  backend {
    group = google_compute_region_network_endpoint_group.site_neg.id
  }

  enable_cdn      = false
  security_policy = google_compute_security_policy.main.self_link

  log_config {
    enable      = true
    sample_rate = 0.1
  }
}

resource "google_compute_backend_service" "admin" {
  count     = var.admin_subdomain != "" ? 1 : 0
  name      = "${var.app_name}-admin-backend"
  project   = var.project_id
  protocol  = "HTTP"
  port_name = "http"

  backend {
    group = google_compute_region_network_endpoint_group.admin_neg[0].id
  }

  enable_cdn      = false
  security_policy = google_compute_security_policy.main.self_link

  log_config {
    enable      = true
    sample_rate = 1.0 # admin 트래픽은 적고 감사 가치가 높다 — 전수 로깅.
  }
}

# ── URL Map ────────────────────────────────────────────────────────
resource "google_compute_url_map" "main" {
  name    = "${var.app_name}-url-map"
  project = var.project_id

  # 미매칭 호스트(직접 IP 접근 등)도 site로 — 자산/민감 표면 노출 없음.
  default_service = google_compute_backend_service.site.id

  host_rule {
    hosts        = [var.domain]
    path_matcher = "site"
  }

  path_matcher {
    name            = "site"
    default_service = google_compute_backend_service.site.id

    # 백엔드 FastAPI 표면은 /api/v1/* 뿐이다. site 자신의 Next route handler
    # (/api/leads, /api/revalidate)는 /api/v1 밖이므로 site로 남는다.
    path_rule {
      paths   = ["/api/v1/*"]
      service = google_compute_backend_service.api.id
    }
  }

  dynamic "host_rule" {
    for_each = var.admin_subdomain != "" ? [1] : []
    content {
      hosts        = [var.admin_subdomain]
      path_matcher = "admin"
    }
  }

  dynamic "path_matcher" {
    for_each = var.admin_subdomain != "" ? [1] : []
    content {
      name            = "admin"
      default_service = google_compute_backend_service.admin[0].id
    }
  }
}

# ── HTTP → HTTPS Redirect ──────────────────────────────────────────
resource "google_compute_url_map" "http_redirect" {
  name    = "${var.app_name}-http-redirect"
  project = var.project_id

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "http_redirect" {
  name    = "${var.app_name}-http-redirect-proxy"
  project = var.project_id
  url_map = google_compute_url_map.http_redirect.id
}

resource "google_compute_global_forwarding_rule" "http_redirect" {
  name       = "${var.app_name}-http-redirect-rule"
  project    = var.project_id
  target     = google_compute_target_http_proxy.http_redirect.id
  ip_address = google_compute_global_address.lb_ip.address
  port_range = "80"
}

# ── HTTPS Proxy + Forwarding Rule ──────────────────────────────────
resource "google_compute_target_https_proxy" "main" {
  name    = "${var.app_name}-https-proxy"
  project = var.project_id
  url_map = google_compute_url_map.main.id

  ssl_certificates = [google_compute_managed_ssl_certificate.main.id]
}

resource "google_compute_global_forwarding_rule" "https" {
  name       = "${var.app_name}-https-rule"
  project    = var.project_id
  target     = google_compute_target_https_proxy.main.id
  ip_address = google_compute_global_address.lb_ip.address
  port_range = "443"
}

# ── DNS Record (optional — Cloud DNS) ──────────────────────────────
resource "google_dns_record_set" "a" {
  count        = var.dns_zone_name != "" ? 1 : 0
  name         = "${var.domain}."
  type         = "A"
  ttl          = 300
  managed_zone = var.dns_zone_name
  project      = var.project_id
  rrdatas      = [google_compute_global_address.lb_ip.address]
}

resource "google_dns_record_set" "www" {
  count        = var.dns_zone_name != "" ? 1 : 0
  name         = "www.${var.domain}."
  type         = "CNAME"
  ttl          = 300
  managed_zone = var.dns_zone_name
  project      = var.project_id
  rrdatas      = ["${var.domain}."]
}

resource "google_dns_record_set" "admin" {
  count        = var.dns_zone_name != "" && var.admin_subdomain != "" ? 1 : 0
  name         = "${var.admin_subdomain}."
  type         = "A"
  ttl          = 300
  managed_zone = var.dns_zone_name
  project      = var.project_id
  rrdatas      = [google_compute_global_address.lb_ip.address]
}

# ── Cloud Armor (WAF) — 엣지 보안 정책 ─────────────────────────────
# INFRA-1: Was a no-op default-allow. Now enforces:
#   1) per-IP rate-based ban for abuse/bot flood protection (edge defense),
#   2) preconfigured OWASP SQLi/XSS WAF rules in PREVIEW (observe first; flip
#      preview=false to enforce once logs confirm no false positives),
#   3) a lowest-priority default allow so legitimate traffic still passes.
resource "google_compute_security_policy" "main" {
  name    = "${var.app_name}-security-policy"
  project = var.project_id

  # 1) Rate-based ban: throttle then ban a single IP that floods the edge.
  rule {
    action   = "rate_based_ban"
    priority = 1000
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 600
        interval_sec = 60
      }
      ban_duration_sec = 600
      ban_threshold {
        count        = 1200
        interval_sec = 60
      }
    }
    description = "Per-IP rate-based ban (>600 req/min throttled, >1200 req/min banned 10m)"
  }

  # 2) Preconfigured OWASP WAF rules — PREVIEW mode (log-only) to start.
  #    Flip preview=false after reviewing Cloud Armor logs for false positives.
  rule {
    action   = "deny(403)"
    priority = 1100
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable')"
      }
    }
    preview     = true
    description = "OWASP SQLi protection (preview — observe before enforcing)"
  }

  rule {
    action   = "deny(403)"
    priority = 1200
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable')"
      }
    }
    preview     = true
    description = "OWASP XSS protection (preview — observe before enforcing)"
  }

  # 3) Default allow — lowest priority fallback.
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow"
  }
}

# Output the LB IP for DNS setup
output "load_balancer_ip" {
  value = google_compute_global_address.lb_ip.address
}

output "api_url" {
  description = "Public API entrypoint through the HTTPS load balancer"
  value       = "https://${var.domain}"
}
