# Re:putation — 전체 GCP 배포 런북 (2026-06-09)

> **이 문서가 현행 런북이다.** 이전 런북(`2026-06-08-deployment-runbook.md`)은
> site/admin을 Vercel에 두는 구성 기준이라 §5(Vercel)가 더 이상 적용되지 않는다.
> 백엔드 절차(§1~§4)는 대부분 유효하므로 참조용으로 유지.

## 아키텍처 (전체 GCP)

```
User
 └─ HTTPS LB (managed cert: <domain> + <admin_subdomain>, Cloud Armor)
     ├─ host <domain>
     │   ├─ /api/v1/*  → Cloud Run: reputation-api   (FastAPI — public+admin API, 자산 서빙)
     │   └─ 그 외       → Cloud Run: reputation-site  (Next.js — 콘텐츠 허브, /api/leads, /api/revalidate)
     └─ host <admin_subdomain>
         └─ 전부        → Cloud Run: reputation-admin (Next.js BFF — 세션 인증, API proxy)

내부: reputation-worker / reputation-beat (Celery) → VPC connector → Memorystore Redis
      api/worker/beat → Cloud SQL (/cloudsql socket)
프론트엔드 SA(reputation-frontend-sa): 자기 secret 4개 + 로그만 (DB/Redis/Vertex 권한 없음)
```

요청 흐름 요점:
- **site → backend**: 서버사이드(SSG/ISR) fetch가 `https://<domain>/api/v1/public/...`
  으로 LB를 다시 경유한다. 브라우저의 자산 요청(`next/image`)도 동일 경로.
- **admin → backend**: BFF가 `https://<domain>/api/v1/admin/...` + `X-Admin-Key`.
- **방문자 IP**: GCP LB는 XFF에 `<client>, <lb>`를 append → Next 쪽은
  second-from-right를 취하고(`lib/client-ip.ts` / `admin/lib/security.ts`),
  site BFF는 `X-BFF-Auth`+`X-Visitor-IP`(SITE_BFF_SECRET)로 backend에 인증 전달.
- **ISR 캐시**: site `max_instances=1`이 기본. 발행 즉시 반영(on-demand revalidate)은
  단일 인스턴스 캐시만 비우기 때문 — 올리려면 공유 cacheHandler 도입 또는
  최대 1h(시간 기반 revalidate) 지연 수용 필요. `terraform/variables.tf` 참조.

---

## 0. Pre-flight

- [ ] `gcloud config get-value project` / `gcloud auth list` 확인.
- [ ] **Secret Manager 9개 secret** (terraform이 8개를 정의 + DB_PASSWORD):
      ```
      for s in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY SLACK_WEBHOOK_URL \
               ADMIN_SECRET_KEY ADMIN_SESSION_SECRET DB_PASSWORD \
               SITE_REVALIDATE_SECRET SITE_BFF_SECRET; do
        gcloud secrets describe $s >/dev/null 2>&1 && echo "✓ $s" || echo "✗ MISSING $s"
      done
      ```
      `SITE_REVALIDATE_SECRET`/`SITE_BFF_SECRET`은 전체 GCP 구성에서 **필수**가 됐다
      (site Cloud Run 서비스가 secret 마운트를 요구).
- [ ] 조직 정책 확인: `constraints/iam.allowedPolicyMemberDomains`가 걸려 있으면
      `allUsers` invoker(terraform `cloudrun_frontend.tf`)가 거부된다 → 예외 등록
      또는 LB에 IAP를 붙이는 구성으로 대체.
- [ ] Cloud SQL 백업/스냅샷.
- [ ] Artifact Registry repo `reputation` 존재 (`scripts/setup-gcp.sh`).

## 1. 이미지 빌드 & 푸시 (3종)

NEXT_PUBLIC_* 값은 **빌드 시점에 번들로 인라인**되므로 도메인이 정해진 뒤 빌드한다.

```bash
REGION=us-central1; PROJECT=$(gcloud config get-value project)
BASE=${REGION}-docker.pkg.dev/${PROJECT}/reputation
TAG=$(date +%Y%m%d-%H%M%S)
DOMAIN=reputation.co.kr            # 실제 도메인으로

# backend
docker build --platform linux/amd64 -t ${BASE}/reputation:${TAG} -f backend/Dockerfile backend
# site
docker build --platform linux/amd64 \
  --build-arg NEXT_PUBLIC_API_URL=https://${DOMAIN}/api/v1/public \
  --build-arg NEXT_PUBLIC_SITE_URL=https://${DOMAIN} \
  --build-arg NEXT_PUBLIC_BACKEND_URL=https://${DOMAIN} \
  -t ${BASE}/site:${TAG} -f site/Dockerfile site
# admin
docker build --platform linux/amd64 \
  --build-arg NEXT_PUBLIC_BACKEND_URL=https://${DOMAIN} \
  -t ${BASE}/admin:${TAG} -f admin/Dockerfile admin

docker push ${BASE}/reputation:${TAG}
docker push ${BASE}/site:${TAG}
docker push ${BASE}/admin:${TAG}
```

푸시 후 **digest를 받아 terraform 변수로 사용** (INFRA-2, 불변 배포):
```bash
gcloud artifacts docker images describe ${BASE}/site:${TAG} --format='value(image_summary.digest)'
```

## 2. Terraform apply

```bash
cd terraform
terraform init -backend-config="bucket=${PROJECT}-tfstate"
terraform apply \
  -var project_id=${PROJECT} \
  -var domain=${DOMAIN} \
  -var admin_subdomain=admin.${DOMAIN} \
  -var api_image=${BASE}/reputation@sha256:... \
  -var site_image=${BASE}/site@sha256:... \
  -var admin_image=${BASE}/admin@sha256:...
```

apply가 만드는 것: VPC/커넥터, Cloud SQL, Redis, 버킷, secret IAM,
Cloud Run 5종(api/worker/beat/site/admin), **run.invoker(allUsers) 3종**
(api/site/admin — 없으면 LB 뒤에서도 403), LB(host 라우팅 + managed cert),
Cloud Armor, (옵션) Cloud DNS 레코드.

주의:
- managed cert는 도메인 셋 해시가 이름에 들어가 도메인 변경 시 create-before-destroy로
  교체된다. **신규 cert provisioning(최대 ~30분) 동안 DNS가 LB IP를 가리켜야 한다.**
- 기존에 apply된 환경이라면 secret IAM이 binding→member로 재생성된다(무중단, 권한 동일).

## 3. DNS

`terraform output load_balancer_ip` → 등록기관에서:
- `<domain>` A → LB IP
- `admin.<domain>` A → LB IP
- (Cloud DNS를 쓰면 `dns_zone_name` 변수로 terraform이 자동 생성)

cert ACTIVE 확인: `gcloud compute ssl-certificates list`.

## 4. DB 마이그레이션 + admin 계정 시드

```bash
bash scripts/deploy.sh migrate      # alembic upgrade head (Cloud Run Job)

# admin_users 시드 (AUTH-4) — backend 이미지의 SERVICE=seed-admin 사용:
gcloud run jobs create reputation-seed-admin \
  --image=${BASE}/reputation:${TAG} --region=${REGION} \
  --service-account=reputation-sa@${PROJECT}.iam.gserviceaccount.com \
  --vpc-connector=reputation-vpc-connector \
  --set-cloudsql-instances=$(terraform -chdir=terraform output -raw database_connection_name) \
  --set-env-vars="SERVICE=seed-admin,APP_ENV=production,ADMIN_EMAIL=ae@motionlabs.kr,ADMIN_NAME=AE,GCP_PROJECT_ID=${PROJECT},DB_USER=reputation,DB_NAME=reputation,CLOUD_SQL_CONNECTION_NAME=$(terraform -chdir=terraform output -raw database_connection_name)" \
  --set-secrets="ADMIN_PASSWORD=ADMIN_OWNER_PASSWORD:latest,DB_PASSWORD=DB_PASSWORD:latest"
gcloud run jobs execute reputation-seed-admin --region=${REGION} --wait
```
(`ADMIN_OWNER_PASSWORD` secret을 미리 생성. 비밀번호는 14자 이상.)

## 5. 배포 검증 체크리스트

- [ ] `https://<domain>/api/v1/public/hospitals` → 200 JSON (API via LB path rule)
- [ ] `https://<domain>/` → 랜딩 렌더, CSP 콘솔 에러 없음
- [ ] `https://<domain>/<clinic-slug>/` → 허브 렌더 + JSON-LD + 이미지(자산 경로가
      `https://<domain>/api/v1/public/...`으로 절대화돼 있는지)
- [ ] `https://admin.<domain>/login` → 로그인 → 병원 목록 (BFF→API 왕복)
- [ ] 리드 폼 제출 → Slack 알림 + admin 리드 목록에서 `consent_ip`가 실제 방문자 IP인지
      (BFF visitor-IP 헤더 + backend `SITE_BFF_SECRET` 양쪽 설정 필요)
- [ ] 콘텐츠 발행 → site 페이지 즉시 갱신 (revalidate 경로:
      backend → `https://<domain>/api/revalidate` → site 서비스)
- [ ] Cloud Logging에서 5개 서비스 로그 확인, Cloud Armor 로그에 WAF preview 매치 검토

## 6. 롤백

이미지 digest 고정 배포이므로 직전 digest로 `terraform apply` 또는
`gcloud run services update-traffic <svc> --to-revisions=<prev>=100`.

## 운영 준비 패스 3에서 해결된 런타임 블로커 (참고)

배포 전 리뷰에서 발견·수정된 항목 — 코드/terraform에 이미 반영돼 있어 추가 조치 불필요:
- **worker/beat가 $PORT 미리슨 → revision ready 실패**: entrypoint가
  `app/workers/health_server.py`를 사이드 프로세스로 기동 + terraform TCP probe.
- **signed URL 서명 실패**: Cloud Run ADC에는 개인키가 없어 IAM signBlob 경유 —
  SA self `roles/iam.serviceAccountTokenCreator`(terraform) + 코드 폴백.
- **자산이 휘발성 로컬 디스크로 저장**: `is_gcs_configured()`가 키 파일만 검사 →
  `K_SERVICE`(Cloud Run) ADC 인식 추가.
- **admin 전 요청 403**: Next standalone의 `nextUrl.origin`이 `localhost:<PORT>`로
  치환돼 same-origin CSRF 체크가 항상 실패 → forwarded 헤더(Host/X-Forwarded-Proto)
  기반 비교로 교체 (standalone 실서버에서 양/음성 케이스 검증).
- **모니터링**: `terraform apply -var alert_email=ops@...` 설정 시 API/site 업타임
  체크(/api/v1/health/live, /) + 이메일 알림 생성.

## 운영 메모

- **scale-to-zero 콜드스타트**: site/admin `min_instances=0` 기본 — 첫 요청 ~수 초.
  데모/영업 기간엔 `site_min_instances=1` 권장.
- **비용 개략(저트래픽)**: Cloud SQL(db-custom-1-3840) + Redis 1GB + VPC connector가
  고정비의 대부분. Cloud Run 5종은 유휴 시 거의 0 (worker/beat min=1은 과금).
- **배포 경로는 하나만**: terraform(권장) 또는 `scripts/deploy.sh` — 혼용 금지
  (서비스 정의가 서로 덮어쓴다). deploy.sh는 site/admin 타깃도 지원하도록 확장됨.
