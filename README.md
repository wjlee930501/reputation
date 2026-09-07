# Re:putation

병원의 공식 자료를 근거가 있는 콘텐츠로 바꾸고, AI 답변의 병원 언급을 측정해 콘텐츠 보완과 월간 보고로 연결하는 MotionLabs의 관리형 서비스다. 운영 목표는 **최소한의 사람 개입, 자동 복구, 필요한 알림만 전달**이다.

문서 버전: **2.0** · 갱신일: **2026-09-07 (Asia/Seoul)**
구현 기준: **`345a6420998bcba21169519cf5ad77600cbfa94b`**

## 먼저 읽을 문서

- [현재 시스템 구조](docs/architecture/system-map.md): 데이터·생성·발행·측정·리포트·복구의 전체 연결과 확인된 한계
- [개발 안내](CLAUDE.md): 변경 시 함께 지켜야 할 계약과 검증 방법
- [문서 인덱스](docs/README.md): 현재 안내와 과거 계획·검수 기록 구분
- [마케터 운영](docs/ops/marketer-operations-runbook.md), [알림 정책](docs/ops/slack-notification-policy.md), [배포·헬스체크](docs/ops/deployment-runbook.md)

## 현재 구성

| 영역 | 역할 |
|---|---|
| `backend/` | FastAPI API, PostgreSQL 상태·근거, Celery 작업과 자동 복구, PDF |
| `admin/` | Next.js 내부 운영 콘솔과 인증 BFF |
| `site/` | Next.js 공통 병원 사이트·ISR·호스트 라우팅·검색 표면·무료 진단 |
| `terraform/` | GCP Cloud Run·Cloud SQL·Redis·Load Balancer·저장소 정의 |
| `scripts/` | 배포·헬스체크·연결 예산·문구 검사 |

API/Worker/Beat/Admin/Site는 모두 Cloud Run에 배포한다. 병원별 HTML 파일을 새로 만드는 구조가 아니라 공통 Site가 공개 자격을 통과한 병원 데이터를 렌더링한다. Next 잠금 버전은 16.3.1이다. API의 async DB와 Worker의 sync DB가 공존한다.

자료 처리 → 근거 추출 → Essence 자동 검토·승인 → 일정·노출 타깃 계획 → 본문·이미지 생성 → 안전 검사 → 자동 발행이 콘텐츠 흐름이다. 월간 측정은 고정 질문·플랫폼과 반복 시도를 보존하며, 내부용·원장용 PDF 및 전달 기록을 구분한다. 정상 생성·발행은 Slack으로 매번 알리지 않는다.

## 로컬 실행과 검증

Docker Compose, Python 3.11 환경, 프론트엔드 테스트에 필요한 Node 환경을 준비한다. 최초 환경은 `.env.example`을 참고하되 실제 설정 정의는 [config.py](backend/app/core/config.py)에서 확인한다. 로컬은 `APP_ENV=development`로 설정하고 비밀 값을 커밋하지 않는다.

```bash
# .env가 이미 있으면 보존한다.
test -f .env || cp .env.example .env
docker compose up -d
docker compose exec api alembic upgrade head
```

API 문서는 `http://localhost:8000/docs`, Flower는 `http://localhost:5555`다. 외부 모델 키가 없으면 생성·측정까지 정상 작동하지 않는다. `make setup`도 있지만 기존 `.env`를 덮어쓰므로 신규 환경에서만 사용한다.

```bash
make test-local           # backend + frontend + copy guard
make test-backend-local   # DB 예산 검사, ruff, pytest
make test-frontend        # Site/Admin test, lint, typecheck
make build-frontend      # Site/Admin production build
make copy-guard
```

Backend 통합 검증은 테스트 PostgreSQL/Redis와 PDF 의존성이 필요하다. skip이 있는 결과를 전체 통합 검증 완료로 보고하지 않는다. 호스트의 `make test-backend-local`이 전체 스위트 진입점이며, Docker의 `make test`에는 저장소 마운트·테스트 DB 연결에 관한 알려진 제약이 있다. 자세한 내용은 [Makefile](Makefile)과 [CI](.github/workflows/ci.yml)를 본다.

## 운영 설정·배포

콘텐츠 기본 모델은 Anthropic Claude, 이미지 기본 경로는 Google Vertex Gemini다. OpenAI/Gemini API로 AI 답변을 측정한다. 정확한 모델 기본값과 배포 환경값의 구분은 [시스템 구조 12절](docs/architecture/system-map.md#12-모델-설정-코드-기본값과-운영값-구분)을 본다.

배포 진입점은 `bash scripts/deploy.sh all`이며, API 배포는 Worker/Beat 호환성·마이그레이션·RedBeat 재조정·준비 검사를 함께 고려한다. 상세 순서와 롤백 한계는 [배포 안내](docs/ops/deployment-runbook.md), 마지막 확인 상태는 [2026-09-07 릴리스](docs/releases/2026-09-07-345a642.md)에 기록했다.

`docs/plans`, `docs/prd`와 Vercel/Supabase 예제는 과거 설계·대체 구성 자료다. 현재 동작이나 배포 환경의 정본으로 사용하지 않는다.
