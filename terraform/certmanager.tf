# ═══════════════════════════════════════════════════════════════════
# Re:putation — Certificate Manager (하이브리드 도메인 cert 평면)
#
#   [기본] {slug}.reputation.motionlabs.kr  → 와일드카드 cert 1장(DNS auth)
#   [옵션] 병원 자기 도메인                  → 도메인별 cert(LB auth) + opt-in map entry
#
#   HTTPS proxy는 cert를 N개 나열(ssl_certificates)하는 대신 certificate_map 1개를
#   붙인다(loadbalancer.tf, var.use_certificate_map). 플랫폼 apex와 와일드카드는
#   명시 hostname 엔트리로 매칭하고, 자기 도메인은 준비된 도메인만 hostname 엔트리.
#
#   설계: docs/plans/2026-06-23-certificate-manager-hybrid-domains.md
# ═══════════════════════════════════════════════════════════════════

# ── DNS authorization (와일드카드 검증용, 1회) ─────────────────────
# var.domain(reputation.motionlabs.kr)에 대한 인증 1개가 도메인 자신과
# *.{domain} 와일드카드를 모두 커버한다. 생성 후 output의 CNAME을 외부 DNS(Route53)에
# 추가해야 cert가 ACTIVE로 전환된다.
resource "google_certificate_manager_dns_authorization" "platform" {
  name    = "${var.app_name}-dnsauth-platform"
  project = var.project_id
  domain  = var.domain
}

# ── 와일드카드 cert (서브도메인 기본) ──────────────────────────────
# *.{domain} 는 단일 라벨을 매칭하므로 admin.{domain}, {slug}.{domain} 모두 커버.
# (admin_subdomain 이 admin.{domain} 형태라는 전제 — 다른 부모면 별도 엔트리 필요.)
resource "google_certificate_manager_certificate" "wildcard" {
  name    = "${var.app_name}-cert-wildcard"
  project = var.project_id

  managed {
    domains            = [var.domain, "*.${var.domain}"]
    dns_authorizations = [google_certificate_manager_dns_authorization.platform.id]
  }
}

# ── 자기 도메인 cert (병원 보유 도메인) ────────────────────────────
# 기존 라이브 도메인은 프록시 cutover 전에 ACTIVE가 되어야 하므로 DNS authorization을 쓴다.
# 신규 opt-in 도메인도 같은 경로를 써서 map entry 추가 전 인증서를 먼저 발급한다.
locals {
  legacy_customer_domain_set          = toset(var.customer_domains)
  certificate_map_customer_domain_set = toset(var.certificate_map_customer_domains)
  certificate_manager_customer_domains = setunion(
    local.legacy_customer_domain_set,
    local.certificate_map_customer_domain_set,
  )
  certificate_map_missing_legacy_domains = setsubtract(
    local.legacy_customer_domain_set,
    local.certificate_map_customer_domain_set,
  )
}

resource "google_certificate_manager_dns_authorization" "customer" {
  for_each = local.certificate_manager_customer_domains
  name     = "${var.app_name}-dnsauth-cust-${substr(md5(each.value), 0, 12)}"
  project  = var.project_id
  domain   = each.value
}

resource "google_certificate_manager_certificate" "customer" {
  for_each = local.certificate_manager_customer_domains
  name     = "${var.app_name}-cust-${substr(md5(each.value), 0, 12)}"
  project  = var.project_id

  managed {
    domains            = [each.value]
    dns_authorizations = [google_certificate_manager_dns_authorization.customer[each.value].id]
  }
}

# ── Certificate map + entries ──────────────────────────────────────
resource "google_certificate_manager_certificate_map" "main" {
  name    = "${var.app_name}-certmap"
  project = var.project_id
}

# PRIMARY = SNI 미매칭 시 기본 cert. 실제 플랫폼 hostnames는 아래 명시 엔트리가 커버.
resource "google_certificate_manager_certificate_map_entry" "primary" {
  name         = "${var.app_name}-entry-primary"
  project      = var.project_id
  map          = google_certificate_manager_certificate_map.main.name
  certificates = [google_certificate_manager_certificate.wildcard.id]
  matcher      = "PRIMARY"
}

resource "google_certificate_manager_certificate_map_entry" "platform" {
  name         = "${var.app_name}-entry-platform"
  project      = var.project_id
  map          = google_certificate_manager_certificate_map.main.name
  certificates = [google_certificate_manager_certificate.wildcard.id]
  hostname     = var.domain
}

resource "google_certificate_manager_certificate_map_entry" "wildcard" {
  name         = "${var.app_name}-entry-wildcard"
  project      = var.project_id
  map          = google_certificate_manager_certificate_map.main.name
  certificates = [google_certificate_manager_certificate.wildcard.id]
  hostname     = "*.${var.domain}"
}

resource "google_certificate_manager_certificate_map_entry" "customer" {
  for_each     = local.certificate_map_customer_domain_set
  name         = "${var.app_name}-entry-${substr(md5(each.value), 0, 12)}"
  project      = var.project_id
  map          = google_certificate_manager_certificate_map.main.name
  certificates = [google_certificate_manager_certificate.customer[each.value].id]
  hostname     = each.value
}

# ── Outputs ────────────────────────────────────────────────────────
# 와일드카드 cert 검증용 CNAME — 이 값을 Route53(motionlabs.kr)에 추가해야
# google_certificate_manager_certificate.wildcard 가 ACTIVE가 된다.
output "platform_dns_authorization_record" {
  description = "Add this CNAME to the motionlabs.kr DNS zone to activate the wildcard cert"
  value       = google_certificate_manager_dns_authorization.platform.dns_resource_record
}

output "certificate_map_id" {
  description = "Attach to the HTTPS proxy via var.use_certificate_map (see loadbalancer.tf)"
  value       = google_certificate_manager_certificate_map.main.id
}

output "customer_dns_authorization_records" {
  description = "Add these CNAME records to each customer domain DNS zone before serving it from the certificate map"
  value = {
    for domain, auth in google_certificate_manager_dns_authorization.customer :
    domain => auth.dns_resource_record
  }
}
