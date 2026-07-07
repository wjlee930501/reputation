# 병원 도메인 — Certificate Manager 하이브리드 이전 계획

> 결정(2026-06-23): cert 평면을 per-domain managed cert → **GCP Certificate Manager(certificate map)** 로 이전.
> 제품 방향 = **하이브리드**: 기본은 우리 서브도메인(`{slug}.reputation.motionlabs.kr`, 와일드카드 1장),
> 자기 도메인(`jangclinic.kr`)은 원하는 병원만 옵션.
> 대체 대상: `docs/plans/2026-06-11-custom-domain-runbook.md`(per-domain cert 스톱갭).

---

## 왜 바꾸나 (현 구조의 한계)

- **라우팅 평면은 이미 N개 대응**: `site/middleware.ts`가 Host → `by-domain` API로 slug 해석 →
  `/{slug}` rewrite. LB URL map은 미매칭 호스트를 전부 site로 흘리고 미들웨어가 처리
  (`terraform/loadbalancer.tf:228` 주석). **도메인 추가 시 코드/host_rule 변경 불필요.**
- **cert 평면이 병목**: 도메인마다 managed SSL cert를 HTTPS proxy에 직접 1개씩 붙임.
  - 하드 캡 **13개** (`terraform/variables.tf:263` validation). 14번째 병원은 `apply` 자체가 거부.
  - 병원 1개 붙일 때마다 `terraform apply` 필요 → AE 셀프서브 불가, 매번 엔지니어 개입.

## 목표 아키텍처 (하이브리드)

```
[기본] {slug}.reputation.motionlabs.kr
   DNS: *.reputation.motionlabs.kr  A  <LB IP>        (Route53, 1회)
   TLS: 와일드카드 managed cert (*.reputation.motionlabs.kr + reputation.motionlabs.kr)
        via Certificate Manager DNS authorization                 (1회)
   라우팅: 미들웨어가 서브도메인 첫 라벨 = slug 로 해석 (by-domain API도 호환)
   → 병원 ACTIVE 되면 즉시 라이브, 고객 DNS·cert 작업 0

[옵션] jangclinic.kr / www.jangclinic.kr
   DNS(고객): CNAME www → cname.reputation.motionlabs.kr  (= A <LB IP>)
              또는 apex A → <LB IP>
   TLS: 도메인별 Certificate Manager managed cert (LB authorization) + map entry
   라우팅: 미들웨어 by-domain 조회 (기존 그대로)
   → 원하는 병원만. cert는 온보딩 시 발급(자동화 가능), 13개 캡 없음(map 수백~수천)
```

핵심 전환:
- HTTPS proxy의 `ssl_certificates`(N개 나열) → **`certificate_map`(1개)** 로 교체.
- 와일드카드 cert를 map의 **PRIMARY** 엔트리로 → 메인/admin/모든 서브도메인을 한 장이 커버
  (`*.reputation.motionlabs.kr` 는 `admin.reputation.motionlabs.kr` 도 매칭 — 단일 라벨).
- 자기 도메인은 hostname별 map entry로 추가.

---

## Route53 (motionlabs.kr — AWS Route53) 1회 작업

| 레코드 | 타입 | 값 | 용도 |
|--------|------|-----|------|
| `*.reputation.motionlabs.kr` | A | `<LB IP>` (현재 `34.117.192.90`) | 서브도메인 기본 — 모든 병원 |
| `cname.reputation.motionlabs.kr` | A | `<LB IP>` | 자기 도메인 CNAME 타겟 |
| `_acme-…`(DNS auth가 지정) | CNAME | (terraform output `dns_authorization` 값) | 와일드카드 cert 검증 |

> 명시 레코드(`admin`, `cname`)는 와일드카드보다 우선하므로 충돌 없음.
> DNS auth CNAME 이름/값은 `google_certificate_manager_dns_authorization` 생성 후 output으로 확인.

### Current production state (2026-07-01)

Production HTTPS now serves through the GCP Certificate Manager certificate
map. Route53 covers the platform apex, wildcard hospital subdomains, and the
stable customer CNAME target. Gabia contains the fixed DNS authorization CNAME
for `jangclinic.kr`. The target HTTPS proxy has `certificateMap` attached and
no legacy `sslCertificates` entries.

Route53 records verified:

| 레코드 | 타입 | 값 |
|--------|------|-----|
| `_acme-challenge.reputation.motionlabs.kr` | CNAME | `180d7b95-8202-451d-8505-3fcc2ea5615a.11.authorize.certificatemanager.goog.` |
| `*.reputation.motionlabs.kr` | A | `34.117.192.90` |
| `cname.reputation.motionlabs.kr` | A | `34.117.192.90` |

Verification evidence:

```bash
dig @8.8.8.8 +short _acme-challenge.reputation.motionlabs.kr CNAME
# 180d7b95-8202-451d-8505-3fcc2ea5615a.11.authorize.certificatemanager.goog.

dig @8.8.8.8 +short cname.reputation.motionlabs.kr A
# 34.117.192.90

dig +trace _acme-challenge.reputation.motionlabs.kr CNAME
# Authoritative Route53 answer from motionlabs.kr zone returns the expected CNAME.
```

Created GCP resources:

| 리소스 | 상태 |
|--------|------|
| Certificate map `reputation-certmap` | attached to `reputation-https-proxy` |
| Certificate `reputation-cert-wildcard` | `ACTIVE` |
| DNS authorization `reputation-dnsauth-cust-53c203f35ef5` (`jangclinic.kr`) | CNAME present in Gabia |
| Certificate `reputation-cust-53c203f35ef5` (`jangclinic.kr`) | `ACTIVE` |
| Map entry `reputation-entry-primary` | `ACTIVE` |
| Map entry `reputation-entry-platform` | `ACTIVE`, explicit `reputation.motionlabs.kr` match |
| Map entry `reputation-entry-wildcard` | `ACTIVE`, explicit `*.reputation.motionlabs.kr` match |
| Map entry `reputation-entry-53c203f35ef5` | `ACTIVE`, explicit `jangclinic.kr` match |

Keep `use_certificate_map = true` and
`certificate_map_customer_domains = ["jangclinic.kr"]` for the production
shape. During the 2026-07-01 cutover, setting `certificate_map` alone left the
old classic `sslCertificates` on the proxy and wildcard hospital subdomains
still served the classic cert. Clearing `sslCertificates` while keeping
`certificateMap` attached made the wildcard map entry serve correctly.

Gabia DNS record required for `jangclinic.kr` Certificate Manager issuance:

| 레코드 | 타입 | 값 |
|--------|------|-----|
| `_acme-challenge.jangclinic.kr` | CNAME | `602ae93a-4399-4b0a-8181-db955c3ddf85.13.authorize.certificatemanager.goog.` |

Verification evidence from the 2026-07-01 cutover:

```bash
dig @ns.gabia.co.kr +short _acme-challenge.jangclinic.kr CNAME
dig @ns1.gabia.co.kr +short _acme-challenge.jangclinic.kr CNAME
dig @ns.gabia.net +short _acme-challenge.jangclinic.kr CNAME
dig @8.8.8.8 +short _acme-challenge.jangclinic.kr CNAME
# 602ae93a-4399-4b0a-8181-db955c3ddf85.13.authorize.certificatemanager.goog.

gcloud certificate-manager certificates describe reputation-cust-53c203f35ef5 \
  --location=global --format='json(managed)'
# managed.authorizationAttemptInfo[0].state: AUTHORIZED
# managed.state: ACTIVE

gcloud certificate-manager maps entries describe reputation-entry-53c203f35ef5 \
  --map=reputation-certmap --location=global --format='value(state)'
# ACTIVE

gcloud compute target-https-proxies describe reputation-https-proxy \
  --global --format=json
# certificateMap: reputation-certmap
# sslCertificates: absent

curl -sS -o /dev/null -w '%{http_code} ssl=%{ssl_verify_result}\n' \
  https://reputation.motionlabs.kr
curl -sS -o /dev/null -w '%{http_code} ssl=%{ssl_verify_result}\n' \
  https://admin.reputation.motionlabs.kr/login
curl -sS -o /dev/null -w '%{http_code} ssl=%{ssl_verify_result}\n' \
  https://jangpyeonhanoegwayiweon.reputation.motionlabs.kr
curl -sS -o /dev/null -w '%{http_code} ssl=%{ssl_verify_result}\n' \
  https://jangclinic.kr
# all returned 200 ssl=0
```

### Route53 upsert handoff

This handoff was completed on 2026-06-30. Keep this section as the recovery
procedure if the records are deleted or drift. When AWS authority is available,
find the hosted zone and apply the batch below:

```bash
aws route53 list-hosted-zones-by-name \
  --dns-name motionlabs.kr \
  --query 'HostedZones[?Name==`motionlabs.kr.`].[Id,Name]' \
  --output table
```

```json
{
  "Comment": "Reputation multi-hospital custom domain bootstrap",
  "Changes": [
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "_acme-challenge.reputation.motionlabs.kr.",
        "Type": "CNAME",
        "TTL": 300,
        "ResourceRecords": [
          {
            "Value": "180d7b95-8202-451d-8505-3fcc2ea5615a.11.authorize.certificatemanager.goog."
          }
        ]
      }
    },
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "*.reputation.motionlabs.kr.",
        "Type": "A",
        "TTL": 300,
        "ResourceRecords": [
          {
            "Value": "34.117.192.90"
          }
        ]
      }
    },
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "cname.reputation.motionlabs.kr.",
        "Type": "A",
        "TTL": 300,
        "ResourceRecords": [
          {
            "Value": "34.117.192.90"
          }
        ]
      }
    }
  ]
}
```

Apply it with the hosted zone id returned above:

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id <HOSTED_ZONE_ID> \
  --change-batch file://docs/plans/route53-reputation-custom-domains.json
```

Then verify DNS and certificate readiness:

```bash
dig +short _acme-challenge.reputation.motionlabs.kr CNAME
dig +short cname.reputation.motionlabs.kr A
dig +short dns-preflight.reputation.motionlabs.kr A
python3 scripts/check_public_dns.py \
  --expected-addresses 34.117.192.90 \
  reputation.motionlabs.kr \
  admin.reputation.motionlabs.kr \
  cname.reputation.motionlabs.kr \
  dns-preflight.reputation.motionlabs.kr
gcloud certificate-manager certificates list \
  --location=global \
  --format='table(name,managed.state,managed.domains)'
```

---

## Terraform 변경 (`terraform/`)

### 1. API 활성화 (`main.tf`)
`google_project_service.services` 에 `certificatemanager.googleapis.com` 추가.

### 2. 와일드카드 cert (서브도메인 기본) — 신규 파일 `certmanager.tf`
```hcl
resource "google_certificate_manager_dns_authorization" "platform" {
  name        = "${var.app_name}-dnsauth-platform"
  project     = var.project_id
  domain      = var.domain            # reputation.motionlabs.kr
}

# 이 cert 1장이 메인 + admin + 모든 {slug}.reputation.motionlabs.kr 커버
resource "google_certificate_manager_certificate" "wildcard" {
  name    = "${var.app_name}-cert-wildcard"
  project = var.project_id
  managed {
    domains            = [var.domain, "*.${var.domain}"]
    dns_authorizations = [google_certificate_manager_dns_authorization.platform.id]
  }
}

output "platform_dns_authorization" {
  value = google_certificate_manager_dns_authorization.platform.dns_resource_record
}
```

### 3. 자기 도메인 cert (LB authorization) — `customer_domains` 재사용
```hcl
resource "google_certificate_manager_certificate" "customer" {
  for_each = toset(var.customer_domains)
  name     = "${var.app_name}-cust-${substr(md5(each.value), 0, 12)}"
  project  = var.project_id
  managed { domains = [each.value] }   # dns_authorizations 없음 → LB authorization
}
```

### 4. Certificate map + entries
```hcl
resource "google_certificate_manager_certificate_map" "main" {
  name    = "${var.app_name}-certmap"
  project = var.project_id
}

# PRIMARY = 와일드카드 (SNI 미매칭 시 기본) → 메인/admin/서브도메인 전원
resource "google_certificate_manager_certificate_map_entry" "primary" {
  name         = "${var.app_name}-entry-primary"
  project      = var.project_id
  map          = google_certificate_manager_certificate_map.main.name
  certificates = [google_certificate_manager_certificate.wildcard.id]
  matcher      = "PRIMARY"
}

resource "google_certificate_manager_certificate_map_entry" "customer" {
  for_each     = toset(var.customer_domains)
  name         = "${var.app_name}-entry-${substr(md5(each.value), 0, 12)}"
  project      = var.project_id
  map          = google_certificate_manager_certificate_map.main.name
  certificates = [google_certificate_manager_certificate.customer[each.value].id]
  hostname     = each.value
}
```

### 5. 프록시 교체 (`loadbalancer.tf`) — `use_certificate_map` 플래그로 무중단 전환
프록시는 `ssl_certificates`(레거시)와 `certificate_map`(신규)을 동시에 가질 수 없다.
`var.use_certificate_map`(기본 false)로 한쪽만 설정한다:
```hcl
resource "google_compute_target_https_proxy" "main" {
  # ...
  ssl_certificates = var.use_certificate_map ? null : concat(
    [google_compute_managed_ssl_certificate.main.id],
    [for cert in google_compute_managed_ssl_certificate.customer : cert.id],
  )
  certificate_map = var.use_certificate_map ? "//certificatemanager.googleapis.com/${google_certificate_manager_certificate_map.main.id}" : null
}
```
컷오버: false로 먼저 apply해 cert map·와일드카드 cert를 깔고(DNS auth CNAME 추가 후
와일드카드 cert ACTIVE 대기) → `use_certificate_map=true`로 flip해 apply. 레거시
customer cert(`google_compute_managed_ssl_certificate.customer`)도 flag로 비활성화된다.

### 6. 정리
- `google_compute_managed_ssl_certificate.main` / `.customer` 제거(컷오버 후).
- `variables.tf` 의 `customer_domains` **13개 cap validation 삭제**, 주석 갱신.
- `google_dns_record_set.*`(Cloud DNS용, 현재 `dns_zone_name=""`라 비활성) — 그대로 둠.

---

## 무중단 컷오버 순서 (메인 도메인 죽이지 않기)

> 원칙: map + PRIMARY 와일드카드 cert가 **ACTIVE** 된 뒤에만 프록시를 `certificate_map`으로 전환.

1. `certificatemanager.googleapis.com` enable (`apply`).
2. `dns_authorization` 생성(`apply`) → output의 CNAME을 **Route53에 추가** → 전파 대기.
3. 와일드카드 cert 생성(`apply`) → `gcloud certificate-manager certificates describe …-cert-wildcard`
   가 `state: ACTIVE` 될 때까지 대기(보통 수~수십 분).
4. `*.reputation.motionlabs.kr` A → LB IP, `cname.reputation…` A → LB IP 를 Route53에 추가.
5. cert map + PRIMARY entry 생성(`apply`).
6. **프록시 전환**: `certificate_map` 설정 + `ssl_certificates` 제거(`apply`).
   → 이 시점 map에 메인/admin이 와일드카드로 이미 커버되므로 무중단.
7. 검증: `https://reputation.motionlabs.kr`, `https://admin.reputation.motionlabs.kr`,
   `https://<아무slug>.reputation.motionlabs.kr` 모두 정상 cert·응답.
8. 구 `google_compute_managed_ssl_certificate.*` 제거(`apply`).

### 롤백
6번 전까지는 구 cert가 프록시에 그대로라 영향 없음. 6번 후 문제 시
`ssl_certificates` 복원 + `certificate_map` 제거로 즉시 원복(구 cert 리소스를 8번 전까지 유지).

---

## 코드 변경 (Phase 2 — 서브도메인 기본 라이브) — ✅ 구현 완료

- **backend by-domain**(`app/api/public/site.py`): `{slug}.{platform host}` 호스트를
  단일 라벨 slug로 역해석(`_platform_subdomain_slug`). 예약 라벨(`www/admin/api/cname/
  static/assets`)·다중 라벨은 제외하고 기존 aeo_domain 경로로 폴백. ACTIVE+site_live 게이트 유지.
  → **site/middleware.ts 변경 불필요**: 미들웨어는 이미 host를 by-domain에 넘기므로
    서브도메인이 자동으로 `/{slug}`로 rewrite된다.
- **활성화 디커플링**(`app/api/admin/hospitals.py` `activate_hospital`): `aeo_domain`이
  있으면 그 DNS를 검증(기존 동작), **없으면 DNS 검증 없이 서브도메인 기본으로 라이브**
  (`verification_method="platform_subdomain"`). 자기 도메인 verify 경로(domain.py /
  operations.py)는 그대로 — 자기 도메인 연결 시에만 사용.
- **Admin 도메인 카드**(`DomainSetupPanel.tsx` / `DomainSetupState.ts` / `DomainSetupTypes.ts`):
  "기본 주소 · 자동 공개: `https://{slug}.{platform host}`" 배너 추가, 카드 제목을
  "자기 도메인 연결 (선택)"으로 강등. `platformSubdomainUrl(slug)` 헬퍼(NEXT_PUBLIC_SITE_URL
  host 또는 기본 `reputation.motionlabs.kr`).
- **canonical**: 자기 도메인 없는 병원은 `site/lib/site-url.ts`가 플랫폼 URL로 폴백(기존
  동작) — 깨지지 않음. 변경 불필요.
- 테스트: `test_public_by_domain.py`(서브도메인 slug 해석·예약 라벨·다중 라벨),
  `test_admin_connect_domain.py`(자기 도메인 없는 활성화) 추가. 백엔드 322 passed.

### 남은 선택 작업
- **자기 도메인 온보딩 자동화**: `customer_domains` 수기 대신 Certificate Manager API로
  cert+entry 런타임 생성 → AE 셀프서브. 1차는 terraform 수기로 시작.

---

## jangclinic(장편한외과) 상태

- `jangpyeonhanoegwayiweon.reputation.motionlabs.kr` 는 wildcard Certificate
  Manager cert로 HTTPS 200/TLS OK.
- `jangclinic.kr` 는 Gabia DNS authorization CNAME, Certificate Manager cert,
  certificate map entry까지 모두 적용되어 HTTPS 200/TLS OK.
- 후속으로 `www.jangclinic.kr`까지 쓰려면 별도 DNS authorization CNAME과
  `www`용 customer cert/map entry를 추가한다. Gabia의 `www → cname.reputation…`
  CNAME만으로는 `www` TLS cert가 자동 발급되지 않는다.

## 한도/비용

- Certificate map: 도메인 수백~수천 규모. per-proxy cert 한도(15) 제약 사라짐.
- 와일드카드 1장이 서브도메인 전원 커버 → 서브도메인 병원은 cert 0개 추가.
- 자기 도메인만 cert 1장씩(LB auth, 자동 갱신).
