# 병원 커스텀 도메인 연결 런북

> 병원이 별도 구입한 도메인(예: `jangclinic.co.kr`)으로 콘텐츠 허브를 서빙하는 절차.
> 관련 코드: `terraform/loadbalancer.tf` (`customer_domains`), site `middleware.ts`(호스트 라우팅),
> backend `GET /api/v1/public/site/hospitals/by-domain/{domain}`, Admin 프로파일 "커스텀 도메인 연결" 카드.

## 아키텍처

```
환자 브라우저 ── https://jangclinic.co.kr
   │   (DNS: jangclinic.co.kr CNAME → aeo.motionlabs.io → LB IP)
   ▼
HTTPS LB ── 도메인별 managed SSL cert (terraform customer_domains)
   │   미매칭 호스트 → default_service = site
   ▼
site (Next.js) middleware
   │   Host 헤더 → by-domain API로 slug 해석 (TTL 캐시)
   │   /            → /{slug} 로 rewrite
   │   /contents 등 → /{slug}/contents 로 rewrite
   ▼
병원 허브 페이지 — canonical/OG/JSON-LD/llms.txt 는 https://{aeo_domain}/{slug}/... 사용
```

- **canonical 정책**: `aeo_domain`이 연결된 병원의 모든 허브 페이지는 커스텀 도메인을
  대표 주소로 선언한다(경로의 `/{slug}` 프리픽스는 유지). 플랫폼 도메인의 `/{slug}` 페이지도
  같은 canonical을 가리켜 중복 콘텐츠/엔티티 분산을 막는다.
- **/api/v1 트래픽**: 커스텀 도메인에는 API host_rule이 없다 — 사이트 페이지만 서빙되고
  백엔드 API는 플랫폼 도메인으로만 노출된다.

## 사전 조건 (1회)

`CNAME_TARGET`(backend config, 기본 `aeo.motionlabs.io`)이 LB IP의 A 레코드로 존재해야 한다:

```
aeo.motionlabs.io.  A  <terraform output load_balancer_ip>
```

## 도메인 1개 연결 절차

| # | 누가 | 작업 |
|---|------|------|
| 1 | AE | Admin → 병원 프로파일 → "커스텀 도메인 연결" 카드에 도메인 저장 |
| 2 | 병원(또는 AE 대행) | 도메인 등록기관에서 CNAME 추가: `{domain} → aeo.motionlabs.io` |
| 3 | 운영자 | CNAME 전파 확인(`dig {domain} CNAME`) 후 `terraform.tfvars`의 `customer_domains`에 도메인 추가 → `terraform apply` |
| 4 | 운영자 | cert 상태 확인: `gcloud compute ssl-certificates list --filter="name~cust"` → ACTIVE 대기 (보통 15–60분, 최대 24h) |
| 5 | AE | Admin에서 [DNS 확인] 클릭 → 사전 단계(V0/허브 빌드/스케줄) 충족 시 site_live/ACTIVE 전환 |
| 6 | AE | `https://{domain}` 접속 확인 — 허브 홈으로 rewrite되는지, 인증서 정상인지 |

## 주의사항

- **순서가 중요**: CNAME(2)이 살아 있어야 cert(3-4)가 ACTIVE가 된다. CNAME 전에 apply하면
  해당 도메인 cert만 PROVISIONING에 머문다(다른 도메인·메인 도메인에는 영향 없음 — cert가
  도메인별로 분리된 이유).
- **도메인 해지/이탈 시**: `customer_domains`에서 제거 후 apply, Admin에서 도메인 비우기.
- **한도**: 도메인별 cert 방식은 HTTPS proxy cert 한도(15) 때문에 **커스텀 도메인 13개까지**.
  그 이상은 Certificate Manager certificate map으로 이전(map entry 방식, 수백 개 규모 지원,
  `gcloud certificate-manager maps ...` 또는 `google_certificate_manager_certificate_map` 리소스로
  proxy의 `certificate_map` 필드에 연결)이 필요하다. 이전 시 메인 도메인 cert도 map으로 옮긴다.
- 커스텀 도메인의 root `/robots.txt`·`/sitemap.xml`은 플랫폼 전역 응답이 그대로 서빙된다.
  병원별 콘텐츠 색인은 canonical과 `/{slug}/llms.txt`(미들웨어가 `/llms.txt`를 rewrite)로 충분하다.
