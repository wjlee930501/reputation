# ═══════════════════════════════════════════════════════════════════
# Re:putation — HTTPS Load Balancer
#
# 아키텍처:
#   User → HTTPS LB (managed SSL cert)
#       → Serverless NEG → Cloud Run API
#       → URL Map: /api/* → API, 나머지 → Vercel (site/admin)
#
# 참고: site/admin은 Vercel에서 서빙되므로 LB의 백엔드는
# Cloud Run API용 Serverless NEG가 메인.
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

# ── Managed SSL Certificate ────────────────────────────────────────
resource "google_compute_managed_ssl_certificate" "main" {
  name    = "${var.app_name}-cert"
  project = var.project_id

  managed {
    domains = [var.domain]
  }
}

# ── Reserved Global IP ─────────────────────────────────────────────
resource "google_compute_global_address" "lb_ip" {
  name    = "${var.app_name}-lb-ip"
  project = var.project_id
}

# ── Backend Service ────────────────────────────────────────────────
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

# ── URL Map ────────────────────────────────────────────────────────
resource "google_compute_url_map" "main" {
  name            = "${var.app_name}-url-map"
  project         = var.project_id
  default_service = google_compute_backend_service.api.id
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
