# 로컬 검증 핸드오프 — 클라우드 세션에서 확인하지 못한 항목

- 대상 브랜치: `claude/comprehensive-architecture-review-c1pwms` (기준 `5522af7` → 42커밋, 212파일)
- 배경: 클라우드 컨테이너에는 **Docker 데몬·Postgres·Redis가 없고 외부 네트워크가 제한**되어
  DB/브로커 의존 테스트 35건(실패 7 + 에러 28)과 컨테이너·CI·실제 공급자 호출을 검증하지 못했다.
  이 35건은 변경 전 기준선에서도 동일하게 실패했으므로 회귀는 아니지만, **이번 변경이 직접 건드린
  DB 경로**가 여럿 포함되어 있어 로컬에서 반드시 한 번 돌려야 한다.
- 클라우드에서 확인 완료: 백엔드 유닛 2,198 통과, Admin 511·Site 296·scripts 79 통과, 양쪽
  typecheck·lint 통과, WeasyPrint 실렌더(`REQUIRE_PDF_RENDER=1`) 통과, site `next build` 통과.

관련 문서: `docs/reviews/2026-09-01-comprehensive-architecture-review.md`(위생·비용),
`docs/reviews/2026-09-01-value-alignment-review.md`(가치 정합성), 갱신된 `CLAUDE.md`.

---

## 로컬 검증 결과 (2026-09-02, macOS 26.5 / Docker 29.2.1 / Postgres 16 / Redis 7)

**1장(반드시 확인)은 전부 실행했고 지금은 전 항목 통과다.** 처음 돌렸을 때 21건이 깨졌고,
그중 3건은 이 브랜치가 만든 진짜 회귀였다. 아래가 최종 상태다.

| 항목 | 결과 |
|---|---|
| 1-1 전체 백엔드 스위트 (Postgres+Redis, `REQUIRE_PDF_RENDER=1`) | **2,645 통과 / 2 skip / 실패 0** |
| 1-2 마이그레이션 왕복 (`head → 0060 → head`) | 통과 |
| 1-2 autogenerate 드리프트 | **`add_column`/`drop_column` 0건** (main 기준선은 각 4건 → 0053/0054 컬럼 복원 확인) |
| 1-3 Docker 배선 (build/up/entrypoint/queue/health) | 통과 |
| 1-3 `make migrate` | 통과 |
| 1-3 `make test` | **실패 → 수정함** (아래 D) |
| 1-4 CI 커버리지 게이트 (`--cov-fail-under=60`) | 통과 (83.25%) |
| 프론트 admin/site test·typecheck·lint·build | 전부 통과 (`npm ci` 양쪽 클린) |
| 가드 (copy-guard / DB 연결 예산 / ruff) | 전부 통과 |
| `terraform fmt -check -recursive` | 통과 |

### 이번 검증에서 고친 것

**A. `operations_center` 파사드 재노출 삭제 — 통합 테스트 13건 붕괴 (회귀)**
`c4f2f54`(데드코드 정리)가 `api/admin/operations_center.py`의 재노출 15개를 지웠는데,
`tests/integration/test_attention_queue.py`가 하위 모듈이 아니라 이 파사드를 통해
`get_operations_overview`·`get_operations_queue` 등을 직접 호출한다. Postgres 게이트가 걸린
테스트라 클라우드에서는 정적 참조만 보고 "미사용"으로 판단됐다. 재노출을 되살리고,
다시 지워지지 않도록 "통합 테스트까지 돌려보고 지워라"는 주석을 남겼다.

**B. `test_incident_service.py:686` — UUID를 VARCHAR 컬럼에 비교 (회귀)**
`AdminAuditLog.target_id`는 `String(80)`이고 `write_audit_log`가 `str(...)`로 저장한다.
새로 추가된 시스템 ACK 테스트만 `str()` 없이 비교해 asyncpg가 `TypeError: expected str, got
UUID`를 던졌다. 같은 파일 300행을 포함해 다른 모든 호출부는 이미 `str()`을 쓴다.

**C. 인시던트 대기열 쿼리 예산 — 2-pass 전환과 어긋난 단언 (회귀)**
WP18이 `load_incidents_queue`를 의도적으로 2-pass(원인 그룹 판별 → 해당 페이지 상세)로
바꿨는데, 기존 단언은 `len(statements) == 1`(그리고 개요 예산 `<= 5`)에 묶여 있었다.
가드의 목적은 "행 수에 비례하지 않는다"(N+1 금지)이지 "정확히 1회"가 아니므로,
2-pass를 반영해 상수 예산으로 갱신했다 — 인시던트 대기열 `== 2`, 개요 `<= 6`,
`many_count == one_count`는 그대로. 테스트 이름도
`test_incident_queue_groups_same_cause_in_a_constant_number_of_queries`로 바꿨다.

**D. `make test`가 컨테이너에서 실행 불가 (이 브랜치가 새로 쓴 레시피)**
`uv sync --locked --extra dev`가 `/opt/venv`를 다시 쓰려다
`Permission denied (os error 13)`로 죽는다. 런타임 스테이지가 빌더의 `/opt/venv`를 chown 없이
복사한 뒤 `appuser`로 전환하기 때문이다. 두 `docker compose exec`에 `-u root`를 붙여 해결.

**E. 인증서 잡 테스트가 전역 outbox 행을 남김 (main에도 있던 선행 문제)**
`tests/integration/test_domain_certificate_jobs.py`의 fixture가 병원 행만 지운다. `incidents`·
`notification_outbox`의 `hospital_id`는 `ON DELETE SET NULL`이라 인시던트/발송 대기 행이
전역(`hospital_id=NULL`) 행으로 살아남고, 뒤에 도는
`test_content_publish_recovery_postgres.py`의 **전역** outbox 배치가 그 행까지 집어 삼켜
`(1,1,1)` 대신 `(2,2,2)`가 된다. main 기준선에서도 동일하게 재현되므로 회귀는 아니지만,
로컬 스위트를 초록으로 만들려면 필요해서 teardown 순서를 고쳤다.

### 이 문서에서 틀렸던 지시 (수정 반영)

- `docker compose up -d db redis` 뒤 "Redis :6379"는 사실이 아니었다 — compose의 `redis`
  서비스에 `ports`가 없어 호스트에서 접속할 수 없고,
  `tests/test_admin_domain.py::test_verify_domain_accepts_lb_address_for_apex_domain`이
  브로커 접속 실패로 깨진다. `docker-compose.yml`의 redis에 `6379:6379`를 노출했다.
- `alembic revision --autogenerate ... --sql`은 alembic이 거부한다
  (`Using --sql with --autogenerate does not make any sense`). 드리프트 확인은 `--sql` 없이
  리비전을 생성한 뒤 `op.add_column`/`op.drop_column`을 세고 파일을 지우는 방식으로 해야 한다.
- macOS에서 `REQUIRE_PDF_RENDER=1`을 돌리려면 `brew install pango`와
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`가 필요하다. 없으면 WeasyPrint가
  `libgobject-2.0-0`을 못 찾아 PDF 테스트 6건이 깨진다 (코드 문제가 아님).

### 남은 관찰 사항 (조치 안 함)

- **autogenerate 잔여 소음**: 컬럼 차이는 0이지만 `alter_column` 48건 + 인덱스 재생성 27건이
  계속 제안된다(JSON/JSONB 표기, 인덱스가 마이그레이션에만 있고 `__table_args__`에 없음).
  **main 기준선과 개수가 완전히 동일**하므로 이 브랜치가 만든 것이 아니다.
- **compose에서 worker/beat 헬스 서버가 안 뜬다**: `docker-entrypoint.sh`의 `"$@"` 분기가
  compose의 `command:`를 그대로 실행하면서 SERVICE 분기(헬스 서버 사이드카)를 건너뛴다.
  Cloud Run은 인자를 주지 않으므로 영향 없다 — `SERVICE=worker`로 인자 없이 직접 띄워
  `/live`·`/ready` 200을 확인했다. 다만 `backend/Dockerfile`의 새 주석("worker/beat도 HTTP
  서버를 띄운다")은 compose 기준으로는 더 이상 맞지 않는다.
- **컨테이너 안 `make test`는 여전히 DB 의존 테스트가 깨진다**: 테스트가 `localhost:5434`를
  하드코딩하는데 컨테이너 안에서는 `db:5432`다. `-u root` 수정으로 pytest 자체는 돌고
  2,195건이 통과한다. 컨테이너 경로를 초록으로 만들려면 테스트의 URL 하드코딩을 걷어내야 하고,
  그건 이 브랜치 범위 밖이다. 로컬 정본은 `make test-backend-local`이다.

### 아직 안 한 것

2장(실제 공급자 호출)과 3장(운영 판단) 전체 — 유료 API 키·프로덕션 DB·GCP 자격증명이 필요해
로컬에서 실행하지 않았다. 3장 중 검증 가능한 것만 확인: 7번 Admin lockfile(`npm ci` 클린),
6번 `terraform fmt -check`(통과)와 `require_no_dropped_terraform_env` 가드(scripts 79건 통과),
2번 백필 CLI(`python -m app.utils.query_target_backfill --hospital-id ...`) 존재 확인.
`terraform plan` 자체는 GCP 자격증명·원격 state가 필요해 실행하지 않았다.

---

## 0. 준비 (한 번)

```bash
git fetch origin && git checkout claude/comprehensive-architecture-review-c1pwms
docker compose up -d db redis                      # Postgres :5434, Redis :6379
cd backend && uv sync --locked --extra dev
# 테스트 DB (integration conftest·*_postgres 테스트가 reputation_test를 기대)
docker compose exec db psql -U reputation -c 'CREATE DATABASE reputation_test;' || true
SYNC_DATABASE_URL=postgresql://reputation:reputation@localhost:5434/reputation_test \
  uv run --no-sync alembic upgrade head           # 0061까지
cd ../admin && npm ci && cd ../site && npm ci && cd ..
```

---

## 1. 반드시 확인 (이번 변경이 직접 건드린 DB·브로커 경로)

### 1-1. 전체 백엔드 스위트 (Postgres·Redis 기동 상태)
```bash
cd backend
INTEGRATION_DATABASE_URL=postgresql://reputation:reputation@localhost:5434/reputation_test \
REQUIRE_PDF_RENDER=1 uv run --no-sync pytest -q
```
기대: 실패 0. 클라우드에서 못 돈 파일 중 **변경과 직접 관련된 것**:

| 파일 | 왜 봐야 하나 | 관련 변경 |
|---|---|---|
| `tests/test_site_revalidation_control_postgres.py` | 올림(publish) 경로 멱등 키 형식 유지 확인. 내림(unpublish) 경로는 유닛으로만 검증 | WP1 비공개 리밸리데이션 복구 (`services/site_revalidation_control.py`) |
| `tests/test_notification_outbox.py` (17 errors) | 개발 채널 라우팅(`SLACK_WEBHOOK_URL_DEV`), 복구 메시지 문구 변경, 자동 ACK | WP12c 알림 위생 |
| `tests/test_incident_service.py`, `tests/test_task_incidents.py` DB 케이스 | `ck_incidents_acknowledgement_fact` 완화 후 시스템 ACK(`acknowledged_by_id=NULL`) 저장 | 마이그레이션 **0061** |
| `tests/test_onboarding_projector_postgres.py` | READY fixture에 `aeo_domain` 부여로 수정됨 — ACTIVATION_READY가 자기 도메인 병원에만 발화하는지 | WP12a 자동 활성화 |
| `tests/test_monthly_report_manual.py` 스킵 9건 | `content_summary["talking_points"]`·`citations` 저장 경로가 실제 DB를 거침 | WP9 인용 귀속, WP11 원장 리포트 |
| `tests/test_monthly_sov_postgres.py`, `test_monthly_artifact_*_postgres.py` | 헤드라인 k/n 정의·매칭 코호트 분모가 manifest 저장 경로와 맞는지 | WP10 통계 |
| `tests/integration/test_essence_auto_review_postgres.py` | 재정 페이로드 축소 후 ESCALATED/APPROVED 판정 불변 | WP8 |
| `tests/integration/test_attention_queue.py` | 인시던트 큐 SQL 페이지네이션(2-pass), `requires_operator_action`, 08:00 전 PUBLISH_DUE 은닉 | WP18, WP12c |
| `tests/integration/test_lead_diagnosis_report_api.py` | status 폴링이 `access_count`를 올리지 않고 report GET만 올리는지 | WP17 |
| `tests/test_operation_run_concurrency.py`, `test_worker_loop_ownership.py`, `test_admin_domain.py` | Redis 필요. 변경과 무관하지만 기준선 실패 목록에 있으므로 함께 확인 | — |

### 1-2. 마이그레이션 왕복 + autogenerate 무변경
```bash
cd backend
export SYNC_DATABASE_URL=postgresql://reputation:reputation@localhost:5434/reputation_test
uv run --no-sync alembic downgrade 0060 && uv run --no-sync alembic upgrade head
uv run --no-sync alembic revision --autogenerate -m "drift-check" --sql | grep -E "op\.(add|drop|alter)" ; echo "(위 출력이 비어야 함)"
```
- 0053/0054가 추가한 컬럼 4개(`content_items.image_policy_verified_at`, `content_focus_topic`,
  `hospitals.hero_specialties`, `content_focus_topics`)를 ORM에 복원했으므로 autogenerate가
  **drop_column을 더 이상 제안하지 않아야** 한다. 생성된 drift-check 리비전 파일은 삭제.
- 0061 downgrade는 시스템 ACK 행이 있으면 거부하도록 설계됨 — 빈 DB에서는 통과해야 한다.

### 1-3. Docker 컨테이너 배선
```bash
docker compose build api worker beat
docker compose up -d
docker compose logs api | head -20        # uvicorn ... --reload 가 실제로 적용되는지 (entrypoint "$@" 통과)
docker compose logs worker | grep -- "-Q "  # default,content,sov,reports,leadgen,certificates
curl -s localhost:8000/health/ready
make migrate && make test                  # dev extra sync 후 컨테이너 안에서 pytest 실행되는지
```
- `backend/docker-entrypoint.sh`에 `"$@"` 분기를 넣어 compose `command:`가 더 이상 무시되지 않는다.
  Cloud Run 경로(인자 없음)는 기존 SERVICE 분기 그대로다.
- worker/beat 컨테이너의 `/live`·`/ready`(health_server, 8080)가 뜨는지도 확인.

### 1-4. CI 워크플로 실제 실행
- `.github/workflows/ci.yml` backend job이 `astral-sh/setup-uv@v10` + `uv sync --locked --extra dev`로
  바뀌었다. 이 브랜치의 PR 또는 push에서 **Actions가 초록인지** 확인. 특히 terraform job의 scripts
  테스트 스텝(pydantic/pydantic-settings 추가 설치)과 커버리지 게이트 60%.
- 클라우드에서는 워크플로를 실행할 수 없어 YAML 정합성만 확인했다.

---

## 2. 실제 공급자·환경으로만 확인 가능한 것

| 항목 | 확인 방법 | 관련 변경 |
|---|---|---|
| **Anthropic 프롬프트 캐시 적중** | 같은 병원 콘텐츠 2편을 5분 안에 생성 후 로그의 `cache_read_input_tokens > 0` 확인 (`content_engine.py` 캐시 계측 로그). 첫 호출은 `cache_creation_input_tokens`만 있어야 정상 | WP7 |
| **Google 이미지 안전 차단 즉시 폴백** | Vertex 응답의 `finish_reason`/`prompt_feedback.block_reason` 필드명이 실제 SDK 응답과 맞는지 — 의도적으로 차단될 제목으로 1회 재현 후 재시도 없이 폴백 프롬프트로 가는지 로그 확인 (`image_engine.py ImageSafetyBlockedError`) | WP8 |
| **자동 채우기 Haiku 전환 품질** | 실제 병원 2~3곳에서 `POST /admin/hospitals/{id}/profile/autofill` 실행, grounding 검증 통과율이 Sonnet 때와 비슷한지. 문제 시 `.env`에 `AUTOFILL_MODEL=claude-sonnet-4-5`로 즉시 되돌림 | WP8 |
| **cost_guard 429** | 킬스위치 켠 상태에서 Admin 자료 처리·운영 기준 초안·자동 채우기 호출 시 429가 오는지 (`tests/test_cost_guard_admin_bypass.py`의 실환경 판) | 위생-A |
| **Slack 개발 채널 분리** | `.env.production`에 `SLACK_WEBHOOK_URL_DEV` 설정. 미설정이면 기존 채널로 폴백되어 인프라 인시던트 감소 효과가 없다 | WP12c |
| **IndexNow·리밸리데이션 실호출** | 글 1건 reject → site 캐시가 재검증 실패 시에도 복구 run이 생기는지(`operation_runs`에 `site-revalidation:{id}:unpublish:{edition}` 키) | WP1 |

---

## 3. 배포 전 운영 판단이 필요한 것 (코드가 아니라 결정)

1. **기존 병원 자동 활성화 백로그** — `site_built=True AND site_live=False AND status='PENDING_DOMAIN' AND aeo_domain IS NULL`인 병원은 배포 후 `reconcile-autonomous-workflows`(1분 주기)가 `build_aeo_site`를 재배달하며 **자동으로 ACTIVE**가 된다. 배포 전에 대상 목록을 뽑아 의도와 맞는지 확인:
   ```sql
   SELECT slug, status, site_built, site_live, aeo_domain FROM hospitals
    WHERE site_built AND NOT site_live AND status='PENDING_DOMAIN' AND (aeo_domain IS NULL OR aeo_domain='');
   ```
   원치 않는 병원은 먼저 PAUSED로 두거나 `aeo_domain`을 채워 수동 경로로 남긴다.
2. **질의 타깃 구조 백필** — 기존 `AIQueryTarget`의 빈 구조 필드는 planner가 지연 백필하지만, 월 25일 슬롯 생성 전에 한 번 전체 백필을 권장:
   ```bash
   cd backend && uv run --no-sync python -m app.utils.query_target_backfill            # 전체
   uv run --no-sync python -m app.utils.query_target_backfill --hospital-id <uuid>      # 특정 병원
   ```
3. **월간 헤드라인 정의 변경** — 10월 1일 리포트부터 8월·9월 모두 "셀당 반복 빈도(k/n), 매칭 코호트" 정의로 재계산되어 전월 비교가 나간다. 8월 리포트(9월 1~7일 생성)는 기준선 달이라 델타가 없어 영향 없음. Admin 리포트 상세 JSON에서 `attempts_used`, `mention_frequency`, `ci95_low/high`, `significance`, `sov_pct_all_cells`, `citations`, `talking_points` 필드가 채워지는지 확인.
4. **원장 PDF 실물 확인** — 실제 병원 1곳의 8월 리포트를 `rebuild=True`로 재생성해 1쪽(3막)과 조건부 2쪽 부록의 레이아웃·트리밍(`view["trimmed"]`)을 눈으로 확인. 검증기는 1쪽 또는 부록 포함 2쪽만 허용한다.
5. **Site 캐시 키 변경** — 콘텐츠 목록 fetch URL이 `?limit=500` 하나로 통일되어 기존 캐시 키(60/200)는 자연 만료된다. 배포 직후 `SITE_REVALIDATE_URL`로 병원 전체 재검증을 한 번 쏘면 깔끔하다.
6. **terraform plan** — `GCP_LOCATION` env 주입 4곳(api/worker/beat/migrate)을 제거했다. `terraform plan`에서 그 변경만 나오는지 확인. `scripts/deploy.sh`의 `require_no_dropped_terraform_env` 가드도 통과해야 한다.
7. **Admin lockfile** — `@types/recharts` 제거로 `admin/package-lock.json`이 재생성됐다. 로컬 `npm ci`가 깨끗이 되는지.

---

## 4. 기타 (선택)

- `next build`: site는 클라우드에서 통과. admin은 typecheck/lint만 확인했으므로 `cd admin && npm run build` 한 번.
- `scripts/deploy.sh`의 preflight(`scripts/test_deploy_preflight.py`, `test_deploy_runtime.py` 79건)는 통과했지만 실제 `gcloud` 호출 경로는 실행하지 않았다.
- 클라우드 세션에서 `git stash`가 워크트리 간 공유되어 에이전트 간 간섭이 한 차례 있었고 모두 복구했다. 로컬 저장소에는 영향 없음(`git stash list`에 남은 항목 없음).

---

## 5. 이번 브랜치 변경 요약 (검증 시 참고)

| 패키지 | 핵심 파일 |
|---|---|
| WP1 비공개 리밸리데이션 복구 | `services/site_revalidation_control.py`, `site_revalidate.py`, `api/admin/content.py`, `workers/tasks.py` |
| WP3 V0 측정 체크포인트 | `workers/v0_checkpoint.py`, `workers/tasks.py` |
| WP7 콘텐츠 엔진 토큰 효율 | `services/content_engine.py`, `workers/tasks.py`(제목 상한) |
| WP8 essence/이미지/autofill 비용 | `services/essence_auto_review.py`, `essence_engine.py`, `content_ai_review.py`, `image_engine.py`, `hospital_profile_autofill.py`, `utils/anthropic_retry.py`, `core/config.py`(`AUTOFILL_MODEL`) |
| 위생-A | `api/admin/essence.py`, `hospitals.py`(cost_guard), `core/celery_app.py`(routes), `models/content.py`, `hospital.py`(0053/0054 컬럼), `utils/production_readiness.py` |
| 인프라 | `backend/docker-entrypoint.sh`, `Dockerfile`, `Makefile`, `.github/workflows/ci.yml`, `.env*.example`, `scripts/test_deploy_runtime.py` |
| 데드코드 | `services/notifier.py`(-22함수), `workers/tasks.py`(Redis 블록), 프론트 고아 3개, `standalone/`, `docs/reputation_guide.html`, `terraform/cloudrun.tf`(GCP_LOCATION) |
| WP9 인용 귀속 | `services/content_citations.py`, `report_attribution.py`, `report_engine.py`, `templates/report.html` |
| WP10 헤드라인 통계 | `services/sov_statistics.py`, `monthly_sov.py`, `monthly_sov_types.py`, `monthly_sov_payload.py`, `monthly_sov_repository.py`, `report_engine.py` |
| WP11 원장 리포트 3막 | `templates/doctor_report.html`, `services/report_engine.py`, `report_artifact_validation.py`, `monthly_events.py`, `notification_milestone_messages.py`, `workers/tasks.py`(V0 대비) |
| WP12a 자동 활성화 | `services/hospital_activation.py`, `workers/tasks.py`(build_aeo_site), `autonomous_recovery.py`, `milestone_onboarding_projection.py`, `onboarding_notifications.py`, admin `DomainSetupPanel.tsx` |
| WP12b 게이트 분리 | `services/post_publish_review_policy.py`, `monthly_content_operations.py`, `api/admin/reports.py`, `schemas/report.py`, admin `ReportEvidence/ReportList` |
| WP12c 알림 위생 | `services/incident_safety.py`, `incidents.py`, `incident_types.py`, `notification_delivery.py`, `notification_messages.py`, `content_publish_notifications.py`, `workers/generation_incident_control.py`, `api/admin/operations_center_*`, **`alembic/versions/0061_*`** |
| WP13 무음 실패 신호 | `workers/tasks.py`(주간 SoV·essence ESCALATED), `services/ops_incident_alerts.py` |
| WP14 격차 기반 슬롯 | `services/query_target_structure.py`, `gap_driven_slots.py`, `content_target_planner.py`, `content_brief.py`, `content_engine.py`, `workers/monthly_slots.py`, `api/admin/content.py`, `query_targets.py`, `utils/query_target_backfill.py` |
| WP16 Admin 폴링 | `admin/app/hospitals/[id]/{onboarding,profile,content}/page.tsx`, `hospital-context.tsx`, `layout.tsx`, `api/admin/handoffs.py`(`hospital_id` 필터) |
| WP17 Site 캐시 | `site/lib/api.ts`, `fetch-policy.ts`, `sitemap-builder.ts`, `app/[slug]/contents/**`, `api/public/site.py`, `diagnosis.py`, `services/essence_readiness.py` |
| WP18 DB 쿼리 | `api/admin/sov.py`, `operations_center_incident_queries.py`, `reports.py`(list_reports), `handoffs.py`(배치) |
| 문서 | `CLAUDE.md`, `docs/ops/slack-notification-policy.md`, `docs/reviews/*` |
