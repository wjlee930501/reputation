# PRD: TEAM 1 — Platform Backend 개선 (PO 검토 반영 v0.3)

> 버전: v0.2 → v0.3 | 작성일: 2026-03-17 | PO 검토: 완료

---

## 목표

프로덕션 첫 고객 수용을 위한 인프라 안정화 + 확장 기반 구축

---

## 1. PDF 리포트 GCS 저장 전환 (P0 — Critical)

**현재**: `/tmp/reports/`에 PDF 저장. Cloud Run 재시작 시 소실.

**변경 사항**:
- `config.py`에 `GCS_REPORTS_BUCKET: str = "reputation-reports"` 추가
- `report_engine.py`:
  - PDF 생성 후 `google.cloud.storage`로 GCS 업로드
  - 경로 패턴: `reports/{hospital_slug}/{filename}`
  - DB `MonthlyReport.pdf_path` → `gs://reputation-reports/reports/{slug}/{file}` 형태 저장
  - `/tmp` 파일은 업로드 후 `os.unlink()` 삭제
  - `image_engine.py`의 GCS 업로드 패턴 참고
- `reports.py` download:
  - `generate_signed_url(expiration=timedelta(hours=1))` 반환
  - Cloud Run 서비스 계정에 `iam.serviceAccounts.signBlob` 권한 필요
- 기존 `/tmp` pdf_path 레코드: 마이그레이션 불필요 (첫 고객 전이라 데이터 없음)

**수용 기준**:
- [ ] PDF 생성 → GCS 업로드 → Signed URL 다운로드 동작
- [ ] 컨테이너 재시작 후에도 리포트 다운로드 가능

## 2. Celery Async 패턴 개선 (P0 — Critical)

**결정: sync SQLAlchemy로 전환 (DB) + asyncio.run() (외부 API만)**

Celery는 본질적으로 sync. 매 태스크마다 `asyncio.new_event_loop()` 생성하는 현재 패턴을 제거한다.

**변경 사항**:
- `core/database.py`에 sync 세션 팩토리 추가:
  ```python
  from sqlalchemy import create_engine
  from sqlalchemy.orm import sessionmaker
  sync_engine = create_engine(settings.SYNC_DATABASE_URL)
  SyncSessionLocal = sessionmaker(bind=sync_engine)
  ```
- `workers/tasks.py`:
  - 모든 태스크 함수를 일반 함수로 전환 (async def → def)
  - DB 호출: `SyncSessionLocal()` 사용
  - 외부 API 호출(SoV 엔진 concurrent): `asyncio.run()` 사용 (태스크당 1회만)
  - `_run()` 헬퍼 완전 제거

**수용 기준**:
- [ ] Worker 로그에 `RuntimeWarning: coroutine was never awaited` 또는 `RuntimeError: event loop already running` 없음
- [ ] V0, nightly, monthly 태스크 각 1회 실행 후 정상 완료 확인
- [ ] `_run()` 헬퍼 코드 완전 제거

## 3. site_builder.py 레거시 정리 (P0)

**결정: `build_content_page` 포함 전체 폐기**

Next.js `/site`가 프로덕션. Public API에서 콘텐츠를 서빙하므로 정적 HTML 생성 불필요.

**변경 사항**:
- `build_aeo_site` 태스크: `site_builder.build_site()` 호출 제거. Hospital 상태만 PENDING_DOMAIN 전환 + Slack 알림
- `publish_content` 엔드포인트: `build_content_page()` 호출 제거 (현재도 try/except로 실패 무시 중)
- `site_builder.py` 파일 유지하되 상단에 `# DEPRECATED — 모든 호출처 제거 완료. 향후 삭제 예정` 주석

**수용 기준**:
- [ ] `build_aeo_site` 태스크에서 site_builder 호출 없음
- [ ] 콘텐츠 발행 시 `build_content_page` 호출 없음
- [ ] Next.js /site의 Public API 경유 서빙 정상 동작

## 4. 에러 모니터링 — Sentry 통합 (P1)

**변경 사항**:
- `pyproject.toml`: `sentry-sdk[fastapi,celery]` 추가
- `config.py`: `SENTRY_DSN: str = ""` 추가
- `main.py`: Sentry FastAPI 통합 (DSN 있을 때만)
- `celery_app.py`: Sentry Celery 통합

**수용 기준**:
- [ ] `SENTRY_DSN` 설정 시 에러가 Sentry에 보고됨
- [ ] 미설정 시 graceful skip

## 5. Public API Rate Limiting (P1)

**변경 사항**:
- `slowapi` + Redis 백엔드 (Cloud Run 다중 인스턴스 대비)
- Public API: IP당 60 req/min
- Admin API: 키당 300 req/min

**수용 기준**:
- [ ] 과도한 요청 시 429 응답
- [ ] Redis 연결 실패 시 rate limit 비활성화 (서비스 유지)

## 6. 다중 AE 인증 (P2)

**변경 사항**:
- `models/user.py`: AE 사용자 모델
- `core/security.py`: JWT (`PyJWT` + `passlib`)
- `POST /admin/auth/login`
- 기존 `X-Admin-Key` fallback 유지 (`AUTH_MODE=jwt|legacy`)
- Alembic 마이그레이션 (신규 테이블)

**제약 조건 명확화**: 기존 테이블 스키마 변경 없음. 신규 테이블 추가는 허용.

**수용 기준**:
- [ ] JWT 로그인 → 토큰으로 Admin API 호출 가능
- [ ] 기존 X-Admin-Key도 동작

---

## 변경하지 않는 것
- 기존 테이블 스키마 (신규 테이블 추가만 허용)
- Celery Beat 스케줄 시간/주기
- E2E 테스트 시나리오 (추가만)
