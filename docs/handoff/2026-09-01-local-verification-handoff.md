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
