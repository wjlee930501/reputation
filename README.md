# Re:putation

병의원이 ChatGPT·Gemini 같은 AI 답변에서 더 잘 이해되고 언급되도록 돕는 AI 노출(AEO) 컨설팅·콘텐츠 운영 시스템이다.
운영사: **MotionLabs Inc.**

전체 제품 플로우(계약 → 프로파일 입력 → V0 리포트 → 콘텐츠 허브 준비 → 운영 기준 승인 → 스케줄 설정 →
활성화 → 콘텐츠 자동 생성/발행 → 월간 리포트)는 [`CLAUDE.md`](./CLAUDE.md)에 상세히 정리되어 있다.
개발 전 반드시 먼저 읽는다.

---

## 아키텍처 개요

```
backend/   FastAPI (Python 3.11) — Admin API + Public API + Celery 워커/스케줄러
admin/     Next.js — AE(운영자)가 병원 프로파일·콘텐츠·스케줄·리포트를 다루는 내부 Admin 콘솔
site/      Next.js — 병원별 공개 정보·콘텐츠 허브 (AI/검색엔진이 참고하는 공개 표면)
terraform/ GCP 인프라 정의 (Cloud Run, Cloud SQL, Redis, Load Balancer, 인증서 등)
scripts/   배포·점검용 셸/파이썬 스크립트 (setup-gcp.sh, deploy.sh, copy-guard 등)
docs/      제품 PRD, 배포 런북, 계획 문서
```

- **backend**: FastAPI + SQLAlchemy(async) + Alembic + PostgreSQL. Celery + Redis로 야간 콘텐츠 생성,
  SoV(AI 답변 언급률) 측정, 월간 리포트 등을 스케줄링한다. Anthropic Claude(콘텐츠 생성),
  OpenAI(SoV 측정 + 이미지 생성), Gemini(SoV 측정), Google Cloud(Imagen 3 폴백 + GCS)를 사용한다.
- **admin**: AE가 병원 온보딩·프로파일 편집·콘텐츠 검토/발행·스케줄 설정·리포트 확인을 수행하는 내부 도구.
- **site**: 병원별 슬러그 라우팅으로 공개 콘텐츠·병원 정보·Schema.org 마크업을 서빙하는 AEO 표면. 별도
  홈페이지 납품물이 아니라 AI가 참고하는 콘텐츠 허브 운영 상태 그 자체다.

## 로컬 퀵스타트

### 사전 준비물
- Docker / Docker Compose
- (로컬 Python 테스트를 직접 돌리려면) Python 3.11 가상환경 — `backend/.venv`
- Node.js (admin/site 프론트엔드 테스트·빌드용)

### 1) 환경 구성 + 서비스 기동
```bash
make setup
```
`.env.example`을 `.env`로 복사하고 `db`/`redis`를 먼저 띄운 뒤 전체 스택(`docker compose up -d`)을 기동,
Alembic 마이그레이션(`alembic upgrade head`)까지 자동 적용한다. 완료되면:
- API 문서: http://localhost:8000/docs
- Flower(Celery 모니터링): http://localhost:5555

`.env` 안의 `REPLACE_ME` 항목(ANTHROPIC_API_KEY, OPENAI_API_KEY, GCP_PROJECT_ID 등)은 실제 키로 교체해야
콘텐츠 생성·SoV 측정·이미지 생성이 정상 동작한다. 전체 환경변수 목록과 설명은 [`.env.example`](./.env.example)을 참고한다.

### 2) 마이그레이션만 다시 적용하고 싶을 때
```bash
make migrate      # alembic upgrade head
make revision     # 새 마이그레이션 파일 생성 (autogenerate)
```

### 3) 테스트 실행
```bash
make test-local        # 백엔드(ruff + pytest) + 프론트엔드(site/admin test·lint·typecheck) + copy-guard
make test-backend-local  # 백엔드만: ruff check backend && pytest
make test-frontend       # site/admin: npm test, npm run lint, npm run typecheck
make copy-guard          # 사용자 노출 문구(의료광고 금지 표현 등) 정적 검사 (scripts/check_user_facing_terms.py)
```
백엔드 테스트만 직접 돌리려면:
```bash
backend/.venv/bin/python -m ruff check backend
backend/.venv/bin/python -m pytest -q
```
(Docker 컨테이너 안에서 돌리려면 `make test`로 `docker compose exec api pytest -v` 실행)

### 기타 유용한 타깃
```bash
make demo-seed          # 데모용 시드 데이터 생성
make essence-backfill   # 기존 병원 콘텐츠 운영 기준(essence) 백필
make admin-create-owner # Admin 콘솔 최초 OWNER 계정 생성/회전
make v0 / build-site / gen-content-now / monthly-report   # 개별 Celery 태스크 수동 실행
```

## 주요 환경변수

모든 환경변수와 발급 링크는 [`.env.example`](./.env.example)에 정리되어 있다. 프로덕션/배포용 값은
[`.env.production.example`](./.env.production.example), Vercel+Supabase 조합 배포는
[`.env.vercel-supabase.example`](./.env.vercel-supabase.example)을 참고한다. 핵심 그룹:

- **DB/Redis**: `DATABASE_URL`, `SYNC_DATABASE_URL`, `REDIS_URL`
- **AI — 콘텐츠 생성**: `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `CLAUDE_MODEL_FAST`
- **AI — SoV 측정**: `OPENAI_API_KEY`, `OPENAI_MODEL_QUERY`, `OPENAI_MODEL_PARSE`, `GEMINI_API_KEY`
- **이미지 생성**: `IMAGE_PROVIDER`(openai/imagen), `OPENAI_IMAGE_MODEL`, GCP `GCP_PROJECT_ID`/`GCP_STORAGE_BUCKET`
- **Slack 알림**: `SLACK_WEBHOOK_URL`
- **Admin 인증**: `ADMIN_SECRET_KEY` 등

## 배포 개요

프로덕션은 GCP Cloud Run(backend API/Worker/Beat) + Cloud Run(admin/site Next.js standalone) +
Cloud SQL + Redis 구성이며, Terraform으로 인프라를 관리한다.

1. **인프라 최초 셋업 (1회)**: `bash scripts/setup-gcp.sh` — Artifact Registry, 서비스 계정, GCP API 활성화 등
2. **Terraform 적용**: [`terraform/`](./terraform) — Cloud Run, Cloud SQL, Redis, Load Balancer, 인증서, 모니터링 정의
   (`terraform.tfvars.example` 참고)
3. **배포**: `bash scripts/deploy.sh {api|worker|beat|site|admin|all|migrate}` 또는 대응하는
   `make deploy-api` / `make deploy-worker` / `make deploy-beat` / `make deploy-all` / `make deploy-migrate`
4. **배포 전 안전장치**: `make db-budget-guard`(Cloud SQL 연결 예산 검사), `make copy-guard`(사용자 노출 문구 검사)

세부 런북과 변경 이력은 [`docs/plans/`](./docs/plans)에서 날짜순으로 확인할 수 있다
(`2026-06-09-gcp-full-deployment-runbook.md`, `2026-06-11-custom-domain-runbook.md`,
`2026-06-23-certificate-manager-hybrid-domains.md` 등).

## 문서 인덱스 (`docs/`)

- [`docs/prd/`](./docs/prd) — 제품 요구사항 정의서 (플랫폼 백엔드, AI 엔진, 프론트엔드, 온보딩 운영, UI/UX 브랜드 등)
- [`docs/plans/`](./docs/plans) — 안정화·배포·도메인 관련 실행 계획 및 런북
- [`docs/sales/`](./docs/sales) — 영업/소개 자료
- [`DESIGN.md`](./DESIGN.md) — `/site` 공개 표면 디자인 소스 오브 트루스

## 코드 규칙 (요약)

전체 규칙은 [`CLAUDE.md`](./CLAUDE.md)에 있다. 핵심만 요약하면:

1. 모든 DB/외부 API 호출은 async
2. 모든 함수에 타입 힌트
3. 콘텐츠 생성 후 의료광고 금지 표현 자동 필터 필수
4. 외부 API 호출은 tenacity로 최대 3회 재시도
5. 주요 이벤트마다 Slack 알림 발송
