# Re:putation 종합 아키텍처 리뷰 — 배선·규칙·연결성·고아 요소·비용 효율

- 기준 커밋: `5522af7` (2026-09-01), `REDBEAT_SCHEDULE_VERSION = 2026-08-30.1`
- 범위: `backend/app` 전체(API·모델·서비스·워커), `admin/`, `site/`, `alembic/`, `scripts/`, `terraform/`, `docker-compose.yml`, `CLAUDE.md`·`docs/`
- 방법: 영역별 전수 코드 읽기 6트랙 + 기계 검사(ruff F401/F811/F841, vulture, `settings.*` 참조 대조, 라우트 트리 실제 import 순회 144개, 프론트 API 경로 리터럴 ↔ FastAPI 라우트 교차 대조, 모듈 import 그래프, Alembic 체인). 모든 항목은 파일:행 근거를 가진다.
- 판정 표기: **[C]** 코드로 직접 확인(CONFIRMED) / **[L]** 정황상 유력(LIKELY). 표기 없는 항목은 [C].

---

## 1. 총평

배선의 "형식적 무결성"은 높다. 라우터 21개 include·경로 중복 0·고아 라우터 파일 0, Celery beat 22개 항목 전부 등록·라우팅·소비 큐 일치, 프론트(admin 약 110개·site 12개)에서 백엔드로 나가는 호출 중 **깨진 호출 0건**, Alembic head 단일, 모델 35개 테이블 전부 마이그레이션 존재, 커밋 전 큐잉(lost-task) 없음, 발행 경로마다 의료광고 필터 적용. 배포 가드(preflight 테스트, 프로덕션 fail-fast, 큐 라우팅 회귀 테스트)도 잘 갖춰져 있다.

문제는 **의미적 정합성과 효율**에 있다. 요약하면 다섯 가지다.

1. **CLAUDE.md가 더 이상 코드를 설명하지 못한다.** 스택(Sonnet 3.5/Imagen 3/GPT-4o/Next 15), 구조 트리(admin API 5개 → 실제 36개, services 5개 → 120개), 데이터 모델, Slack 규격(구현체 4개가 dead code), 금지 표현 목록(14 vs 21), 콘텐츠 분량(600~900자 vs 1800~5200자), 08:00 개별 Slack(미발송), STEP6 stale 승인 규칙(생성은 마지막 승인본 허용)까지 어긋난다. "이 파일이 기준"인 만큼 stale한 부분은 곧 오작동 지시가 된다.
2. **LLM 비용 구조에 큰 절감 여지가 남아 있다.** 프롬프트 캐시 미사용(0건), essence 2차 재정이 1차 페이로드(최대 ≈55k 토큰)를 통째로 재전송, 금지 표현 위반 시 전체 재생성(편당 Sonnet 최대 6회), 기존 제목 목록 무제한 누적, V0 리포트 재시도 시 측정 150회 재수행, Admin 경로 3곳이 cost_guard 예약·킬스위치 우회.
3. **복구 루프와 폴링이 유휴 상태에서도 부하를 만든다.** 1분 주기 4개 + 5분 canary 6개 = 시간당 약 256 태스크, DB 문 약 1,100~1,300건(+병원 수 비례)/시. Admin 온보딩 화면은 대량 처리 중 5초마다 8요청(96 req/min). 운영센터 인시던트 큐는 5-테이블 조인 전체를 메모리에 올린 뒤 슬라이스하고 12초마다 폴링된다.
4. **구조적 중복이 계층 경계를 무너뜨렸다.** 병원 404 헬퍼 16중 복제(공용 `api/deps.py`는 미사용), `_enum_value`·`_display_label`·`_has_public_site` 등 3~4중, JSON 파서 3중, 프로파일→프롬프트 직렬화 3중, 워커가 API 라우터의 private 함수를 import, `tasks.py` 5,785줄·`essence.py` 1,992줄·`onboarding/page.tsx` 2,577줄. 이 거대 파일들은 "소스 텍스트를 grep하는 테스트" 11+19개가 분할을 막고 있다.
5. **고아 요소가 꾸준히 쌓였다.** `notifier.py` 25개 중 22개(≈715줄) 미호출, 마이그레이션 0053/0054가 추가한 컬럼 4개가 모델에 없음, DB enum에 `PLAN_8` 잔존, 2026-08 노원 1회성 태스크 2개가 스케줄 경로에 상시 import, 미라우팅 태스크 5개(우연히 동작), `standalone/`·`docs/reputation_guide.html`·`codereview_v1.docx` 등 참조 0 파일, 프론트 고아 모듈 3개.

---

## 2. 최우선 조치 (심각도순)

| # | 항목 | 영향 | 위치 |
|---|---|---|---|
| 1 | **비공개(reject/unpublish) 후 리밸리데이션 실패 시 재시도 없음** — 복구 계획이 `status == PUBLISHED`를 요구해 비공개 글이 최대 1800~3600초 계속 노출 | 의료광고 리스크 | `services/site_revalidation_control.py:66-71`, `workers/tasks.py:5486-5496` |
| 2 | **마이그레이션↔ORM 드리프트**: 0053/0054가 추가한 `content_items.image_policy_verified_at`, `hospitals.hero_specialties`, `hospitals.content_focus_topics`, `content_items.content_focus_topic`이 모델·코드 어디에도 없음. autogenerate 시 drop_column 생성. 0054는 특정 고객 slug 데이터 UPDATE를 스키마 마이그레이션에 하드코딩 | 데이터 안전 | `alembic/versions/0053*`, `0054*` |
| 3 | **V0 리포트 재시도가 측정 150회를 통째로 재수행** — PDF/GCS/commit 단계 실패 시 `self.retry`가 처음부터, 최대 3배(450호출), cost_guard도 3번 예약 | 비용 | `workers/tasks.py:1601-1777` |
| 4 | **Essence 자동 검수 2차 재정이 1차 페이로드 전체 재전송** (노트 80개 × 1.2k자 ≈ 96k자). refresh 1회당 Haiku 최대 10회, 그중 대용량 8회 | 비용 | `services/essence_auto_review.py:673-712, 865-900` |
| 5 | **콘텐츠 생성에 prompt cache 미사용** — 시스템 프롬프트 3,253자 + 출처 힌트 1,193자 + 병원별 철학 컨텍스트가 매 호출 재과금(입력의 30~40%) | 비용 | `services/content_engine.py:474-479` |
| 6 | **금지 표현·검증 실패 시 전체 재생성**, 수리 프롬프트 없음. tenacity 3 × 생성 루프 2 = 편당 Sonnet 최대 6회 + Haiku 검수 2회 | 비용 | `content_engine.py:406-416, 539-550`, `tasks.py:258, 616-666` |
| 7 | **COST_BLOCKED·공급자 장애 슬롯이 영구 스킵** — 시도 지문(philosophy_id·type·date·target)에 예산 리셋이 반영되지 않아 01/04/07시 야간 복구 배치가 사실상 no-op. `SoftTimeLimitExceeded`도 `except Exception`에 잡혀 지문으로 기록됨 | 발행 누락 | `tasks.py:325-392, 2034, 2046, 2244-2249, 2699-2717` |
| 8 | **Admin write 엔드포인트 약 70개가 계정 게이트 없이 공유 `X-Admin-Key`만으로 통과** — `capture_admin_actor`는 헤더가 있으면서 위조일 때만 403, 헤더 부재는 통과(주석 자인). 프로파일 수정·발행·도메인 연결·리드 PII 목록 포함 | 감사 신뢰성 | `core/security.py:143-149`, `main.py:167-171` |
| 9 | **cost_guard 우회 3곳**: Admin 근거 추출·철학 합성·프로파일 자동 채우기가 `check_and_increment` 없이 실계수만 → 킬스위치 무시 | 비용 | `api/admin/essence.py:646, 1407`, `services/hospital_profile_autofill.py:189` |
| 10 | **docker-compose `command:`가 ENTRYPOINT에 의해 전부 무시** (`docker-entrypoint.sh`에 `"$@"` 없음) → 로컬 API `--reload` 미적용. `make test`는 `--no-dev` 이미지에서 pytest 호출 → 실패 | 개발 생산성 | `docker-compose.yml:26,46`, `backend/docker-entrypoint.sh`, `Makefile:33-34` |
| 11 | **CI가 `uv.lock`을 쓰지 않음** (`pip install -e ".[dev]"`) vs 배포 `uv sync --locked` — 테스트한 의존성 그래프 ≠ 배포 그래프 | 신뢰성 | `.github/workflows/ci.yml:117`, `backend/Dockerfile:35` |
| 12 | **환경 템플릿 3종 값 불일치**: `GOOGLE_IMAGE_MODEL`(2.5 vs 3.1), `LEAD_CONSENT_VERSION`(2026-05 vs 2026-08, 변경 시 기존 동의 무효), `CNAME_TARGET`, `SITE_BASE_URL`. deploy.sh는 env를 전량 교체하므로 템플릿 복사 = 프로덕션 오설정 | 운영 사고 | `.env.production.example:111,119`, `.env.example:131,151` |

---

## 3. 배선(wiring) 상세

### 3.1 백엔드 라우터
- `main.py:172-194` 17개 admin + 4개 public include, 하위 라우터 합성(`operations_center.py:40-42`, `leads.py:34-35`) 정상. 정규화 경로 기준 중복 0. `domain_verification*.py`, `lead_recovery.py`, `operations_center_*_queries.py`는 라우터 없는 헬퍼 모듈.
- **기능 중복 엔드포인트**: `POST …/domain/verify`(`domain.py:134`)와 `POST …/operations/verify-domain`(`operations.py:765`)이 같은 `verify_domain_for_hospital` 호출, 후자는 `response_model` 없이 dict 수동 조립. 프론트는 둘 다 사용(`DomainSetupPanel.tsx:324`, `dashboard/page.tsx:732`). `verify_domain_for_hospital` 함수 자체가 `domain.py:144`와 `domain_verification.py:73`에 동명 존재.
- `by-domain`과 `health/by-domain`(`site.py:88-108` vs `124-150`) 동일 조회 블록 복붙.
- `operations_center.py:44-63` `__all__` 18개 re-export를 import하는 곳 없음(`main.py`는 `.router`만).

### 3.2 Celery
- beat 22개 ↔ `task_routes` ↔ 워커 `-Q default,content,sov,reports,leadgen,certificates`(`docker-entrypoint.sh:16`, `docker-compose.yml:46`) 일치. `tests/test_celery_routing.py`가 "routes → 소비 큐"만 검사.
- **등록됐지만 `task_routes`에 없는 태스크 5개**: `generate_content_image`(`tasks.py:2535`), `process_source_asset_task`(`:1026`), `lead_diagnosis_tasks.recover_lead_diagnosis_measurement`(`:328`), `recover_lead_diagnosis_report`(`:516`), `backfill_indexnow`(`:5700`). 앞 4개는 호출부가 전부 `queue=`를 명시해 우연히 동작. `backfill_indexnow`는 호출부 0 + docstring이 `.delay()`를 안내 → 따르면 아무도 소비하지 않는 `celery` 큐로 감. `production_readiness.EXPECTED_TASKS`(`utils/production_readiness.py:55-98`)도 이 5개 누락.
- beat `options.headers`와 `build_dispatch_headers(...)` 인자는 장식: `before_task_publish`의 stamp가 PURPOSE/TARGET을 `expected_purpose(task_name)`으로 덮어씀(`dispatch_envelope.py:109-125`). headers 없는 3개 항목(`celery_app.py:263-282`)이 정상 동작하는 이유이기도 하다.
- 워커 서비스가 하나이고 6개 큐를 모두 소비(concurrency 2, 인스턴스 1~5)하므로 `celery_app.py:151-153`의 "leadgen 큐 분리로 워커 슬롯 격리"는 명목상. 실제 격리는 `sov_engine.POOL_LEADGEN` 세마포어뿐.
- 모든 태스크 결과가 Redis backend에 저장(`task_ignore_result`/`result_expires` 미설정) → beat 소음만으로 약 6천 키/일.
- `celery_app.py:17` "`--check` 모드가 드리프트를 차단"이라 적혀 있으나 `reconcile_redbeat_schedule --check`는 deploy.sh·CI·Makefile 어디서도 호출되지 않음(`--apply`만).

### 3.3 프론트 ↔ 백엔드
- Admin: 약 110개 경로 전수 대조, 깨진 호출 0. 동적 세그먼트(`operations/${path}`, `pause|resume`, `sessions/revoke`)와 서버가 내려주는 `retry_action.path`·`file_access_url`까지 실제 라우트와 일치.
- Site: 12개 호출 전부 일치, 파라미터 드리프트 없음(진단 요청 필드 1:1, BFF free-text 필드 = 백엔드 validator 대상).
- Admin BFF `ALLOWED_PREFIXES`(`admin/lib/admin-api-proxy-route.ts:17-30`)에 백엔드에 존재하지 않는 루트 prefix 5개(`content, reports, sov, domain, essence`) — 죽은 설정.
- Admin BFF는 `X-BFF-Auth/X-Visitor-IP`를 로그인 라우트에서만 전달(`login-route.ts:79-84`) → 일반 admin 트래픽은 admin Cloud Run 이그레스 IP 하나로 `admin:{ip}` 100/min 버킷에 집계 [L]. 12초 폴링 + 대시보드 8병렬이면 동시 사용자 몇 명에 429 가능.

### 3.4 리밸리데이션·호스트 라우팅
- 백엔드 `site_revalidate.py:189-194` ↔ `site/app/api/revalidate/route.ts:19-20` 헤더·시크릿·경로 집합(`hospital_site_paths :67-84`) 페이지 라우트와 1:1 정합.
- 결함: (a) 비공개 실패 복구 없음(§2-1). (b) essence 경로 5곳(`essence.py:620,692,779,834,1323`)이 `treatments`를 안 넘겨 필러 페이지 미무효화. (c) `/`, `/sitemap.xml`, `/robots`는 dynamic이라 매번 보내는 무효화가 무의미.
- 호스트 라우팅은 인스턴스별 60초 캐시 + 24h stale(`site/proxy.ts:20-24`) — 요청당 백엔드 히트 아님. 건전.

### 3.5 Site 렌더링·데이터 캐시
- 동일 병원 콘텐츠 목록이 `?limit=60/200/500/500&offset=0` **4개 캐시 키**로 분리되어 독립 만료·재요청 → 백엔드 목록 쿼리 4배.
- `/[slug]/contents`는 `searchParams`를 읽어 request-time dynamic(`contents/page.tsx:21,64`) — `revalidate = 3600` 선언 무의미.
- ISR 페이지 6개의 `revalidate = 3600`은 fetch `revalidate: 1800`이 더 낮아 실효 1800 [L].
- sitemap이 병원마다 detail을 추가 fetch(`sitemap-builder.ts:401-414`) — 목록 응답(`site.py:169-190`)에 `treatments`가 없어서.
- 이미지 프록시 1장당 DB 3~4쿼리 + `get_essence_readiness`(소스 자산 전체 로드) + 302(`site.py:334-349`, `assets.py:22-26`). 크롤러·`next/image` 경로라 HTML보다 호출량 큼.
- 백엔드 `PublicApiCacheMiddleware`의 `max-age`는 Next `fetch(next.revalidate)`가 HTTP 캐시 헤더를 쓰지 않으므로 ISR과 무관.

---

## 4. 규칙 준수 — CLAUDE.md 대비 드리프트

| # | CLAUDE.md | 실제 코드 |
|---|---|---|
| 1 | 113행 "Claude Sonnet 3.5" / 343행 `claude-sonnet-4-5` (자기모순) | `config.py` 기본 `claude-sonnet-4-5` |
| 2 | OpenAI `gpt-4o` / `gpt-4o-mini` | `gpt-5.6-luna` / `gpt-4o-mini-2024-07-18`. `deploy.sh:857-890` 게이트가 맨 계열명을 **거부** → CLAUDE.md 값은 배포 불가 |
| 3 | `google-generativeai`, Imagen 3, Imagen 프롬프트 패턴 6종 | `google-genai`(pyproject:30), `GOOGLE_IMAGE_MODEL=gemini-3.1-flash-image` 또는 gpt-image-2(`image_engine.py:200-202`). 프롬프트 패턴 문자열 코드 내 0건. README:25,86도 Imagen 언급 |
| 4 | Site "Next.js 15" | `site/package.json` `next ^16.3.1`, 미들웨어 파일명 `proxy.ts`(Next 16 규약) |
| 5 | 구조 트리: admin API 5파일, `api/admin/schedule.py`, services 5, workers `tasks.py` 단일, models 4, site `[hospital-slug]/` | admin API 36파일, `schedule.py` 없음(`content.py:186,367`), services 120, workers 37 모듈, models 13, `site/app/[slug]/` + ai-diagnosis/llms.txt/api/… |
| 6 | Hospital ~25필드, `plan` 필수; ContentItem status `DRAFT\|READY\|PUBLISHED\|REJECTED` | Hospital 60+필드, `plan: Plan \| None`(`hospital.py:84`); status에 `CANCELLED` 추가, `READY`는 레거시(`content.py:46-49`); SovRecord·MonthlyReport 필드 대폭 확장 |
| 7 | Slack 규격 4종(V0/허브/자동발행/월간) | 구현체 `notifier.notify_v0_report_ready/notify_site_built/notify_content_auto_published/notify_monthly_report_ready` **호출자 0**. 실발송은 `onboarding_notifications.py`·outbox 경로의 "문제→고객 영향→지금 할 일" 포맷(`docs/ops/slack-notification-policy.md:61`) |
| 8 | STEP7 08:00 "Slack → AE: 자동 발행 완료" 개별 알림 | 개별 알림 **미발송**(`tasks.py:2933-2935` 주석 명시), 운영 요약만 |
| 9 | STEP6 "자료 변경 시 기존 승인 stale, 새 승인 전 생성·발행 안 함" | 생성은 `readiness.current or readiness.approved`로 **마지막 승인본 사용**(`tasks.py:263-275`, docstring이 의도라 명시). 발행만 `current` 강제(`:2996`). `GET /essence/philosophy/approved`(`essence.py:1378,1682`)도 snapshot 신선도 무시 |
| 10 | 금지 표현 14개 | `utils/medical_filter.py:16-46` 21개 + 정규식 21종 + NFKC/zero-width 정규화. 프론트(`content/page.tsx:60-80`)는 정규식 19개 복제, 정규화 없음 |
| 11 | 분량 600~900자 | `content_engine.py:37-38,127-128` 1800~5200자(목표 2200~4200), `max_tokens=5500` — 출력 토큰 3~5배 |
| 12 | 주간 "매주 월요일 02:00 전체 병원" | 24일 이후 월요일은 **전면 스킵**(`tasks.py:4245-4251`) + 월간 코호트 제외. 약 4주 중 1주 공백 |
| 13 | 스케줄 절: 23:00/08:00/1일 00:15 | 일치. 단 22:30 stranded 복구, 01·04·07 overnight, 07:45 prepublish, 24~31일 */6h 월간 SoV, 25~31일 슬롯, 화 03:00 네이버, 04:00 리드 파기, 1분 폴러 4종, 5분 canary 6종은 미문서 |
| 14 | 요금제 배분표 | `models/content.py:53-81` 완전 일치. 가격·요금제명은 백엔드에 없고 `site/lib/landing-copy.ts:728-746`, `admin/types/index.ts:475-485`, `admin/lib/onboarding-artifacts.ts:76-80`, `admin/lib/schedule.ts:3-7` 4벌 |
| 15 | 활성화 게이트 3종 | `hospital_lifecycle.py:142-145`, `admin/lib/hospital-activation.ts:38-42` 일치 |
| 16 | 환경변수 20개, `ADMIN_SECRET_KEY=change-me` | `config.py` 95개. `change-me`류 placeholder는 프로덕션 가드가 빈 값만 검사해 통과(`config.py:134-142`) |
| 17 | `SourceType` | admin `types/index.ts:148-160` 12개, 백엔드 `models/essence.py:34-53` 13개 — `PHOTO_BRAND` 누락 |

---

## 5. 기능 연결성 — 8단계 플로우

| STEP | 진입점·배선 | 끊긴 고리 |
|---|---|---|
| 1 계약 | `hospitals/new`(handoff contract/accept), `leads` convert | `POST /admin/handoffs/{id}/correct-contract` 호출부 0 |
| 2 프로파일 | `profile` PATCH, autofill, onboarding | autofill이 cost_guard 우회 |
| 3 V0 | 저장 즉시 dispatch(`hospitals.py:862-877`) ✓ | dispatch 실패 시 `logger.warning`만, incident 없음(`content.py:337-357`은 incident 생성 — 기준 불일치). 재시도 시 측정 재수행 |
| 4 허브 준비 | `dashboard` rebuild-site, `autonomous_recovery` | — |
| 5 활성화 | `profile` 탭 하단 `DomainSetupPanel.tsx:408` | 전용 화면 없음. `site_built` 이동 경로는 profile인데 rebuild 액션은 dashboard |
| 6 운영 기준·스케줄 | `essence`(자동승인 예외), `schedule`, `wiki` | 승인 신선도 판정이 엔드포인트별로 다름(§4-9). `schedule` 탭은 온보딩이 대체하지 않고 링크만 |
| 7 생성·발행 | 23:00 nightly(claim+SKIP LOCKED, 지문, 이미지 재호출 방지) ✓, 08:00 publish | 08:00 개별 Slack 미발송, stale 승인본으로 생성 후 08:00 차단 가능 [L], `_enqueue_overdue_post_publish_review_notifications` 스텁(`tasks.py:2954-2961` `return 0`) |
| 8 월간 리포트 | `run_monthly_reports` */6h 1~7일 | PARTIAL/FAILED 병원은 매 실행 `rebuild=True`로 PDF 2종 재생성 + 배치 `autoretry_for=(Exception,)` ×3 → 병원 1곳당 최대 28×4=112회/월 [L]. 병원별 태스크가 있는데 팬아웃 안 함 |
| 공개 표면 | 승인 데이터만 노출(`site.py:197-200, 290-301, 353-385`), JSON-LD·FAQ·sitemap·robots·llms.txt 동일 소스 ✓ | `site/app/api/leads/route.ts` 호출부 0 → 백엔드 `POST /public/leads` + `SalesLead` 자유문의 경로 전체 dead |

---

## 6. 고아 요소 인벤토리

### 6.1 백엔드 — 함수·모듈
| 항목 | 위치 | 근거 |
|---|---|---|
| `notifier.py` 25개 `notify_*` 중 **22개** 미호출(≈715/1066줄). 라이브: `notify_lead_created`, `notify_lead_diagnosis_received`, `notify_lead_purge_result` | `services/notifier.py` | 테스트 6개 파일이 살려둠 |
| `api/deps.py:get_hospital_or_404` 미사용, 대신 16개 파일에 각자 구현 | `api/deps.py:11` | |
| `_already_done`, `_mark_done`, `_get_redis`, `_redis_client` (CELERY-4 설계 잔재) | `tasks.py:777-812` | app·tests 참조 0 |
| `_query_gemini`, `QUERY_TEMPLATES`, `get_source_text`, `enqueue_onboarding_event`, `enqueue_monthly_event`, `_ensure_brief_capable_exposure_action`, `_needs_attention_clause`, `safe_task_id`, `is_private_asset_ref`, `_medical_risk_rules`, `publication_text`, `ensure_monthly_artifact_failure`, `missing_profile_requirement_labels`, `submit_hospital_pages` | 각 services/api/workers | app 참조 0 |
| 테스트만 참조: `_auto_publish_block_alert_key`, `_raise_if_monthly_report_failures`, `_weekly_manifest_is_resolved`, `segment_mention_rates`, `generate_query_matrix`, `_query_chatgpt_with_search`, `_validate_fetch_url`, `HandoffPendingCreate` | | |
| `validate_philosophy_grounding(require_text_support=)` 미사용 파라미터 | `essence_engine.py:1166` | 주석이 인정 |
| 날짜로 죽은 코드: `nowon_august_backfill.py`, `nowon_orthopedic_faq_regenerate.py`(`tasks.py:4203-4210` 2026-08-26 게이트), `is_august_2026_conversion_window`(`monthly_period.py:88-111`), `_prior_monthly_manifest` 8월 특례(`tasks.py:4811-4816`) | | 매 `monthly_slot_generation`마다 import |
| 프로덕션 병원명 7개 하드코딩 `CONVERSION_HOSPITAL_NAME_TOKENS` | `sov_tracking_set.py:21-29` | 워커 판정에 직접 사용 |
| `utils/ops_control_qa_seed.py`(+`_scenarios`) — Makefile·entrypoint·deploy 어디에도 없음 | | 진입점 고아 |
| `fix_director_credential`, `seed_colon_cluster` — 특정 병원 1회성 수정이 영구 entrypoint | `docker-entrypoint.sh:37-58` | |

### 6.2 백엔드 — 엔드포인트·스키마·컬럼
- 프론트·워커·테스트 어디서도 미호출: `POST …/content/{id}/generation-claim/release`(`operations.py:862`), `POST /admin/handoffs/{id}/correct-contract`(`handoffs.py:203`), incident `assign` ×2(`operations_center_incident_routes.py`).
- 테스트만: `POST …/query-targets/seed-from-matrix`(`query_targets.py:246`; 워커는 내부 함수 직접 import), `GET …/exposure-actions/{id}`(`:190`), `GET …/query-targets/{id}`, `DELETE …/query-targets/{id}`, `DELETE …/variants/{v}`.
- 스키마: `AdminAccountResponse` 동명 이형 2개(`schemas/admin_account.py:93` 9필드 vs `api/admin/auth.py:49` 4필드). 인라인 `BaseModel` 46개가 `api/`에 정의되어 `schemas/`와 이원화. `response_model` 없는 엔드포인트 62/139.
- 컬럼: `hospitals.aeo_site_path`(`hospital.py:135`), `exposure_gaps.diagnosed_at`(`sov.py:281`), `monthly_measurement_manifests.frozen_at`(`monthly_control.py:57`) 참조 0. DB enum `plan`에 `PLAN_8` 잔존(`0001:36`, `0039`는 값 갱신만). 의미 중복: `MonthlyReport.pdf_path/doctor_pdf_path/sent_at` vs `MonthlyReportArtifact.path`/`MonthlyDeliveryEvent`(워커가 이중 write `tasks.py:5207,5228`); `SovRecord.query_id`(legacy `QueryMatrix`) vs `ai_query_target_id` 둘 다 write, 집계는 여전히 legacy 조인(`sov.py:100,133-145`).
- 설정: `GCP_LOCATION`, `LEADGEN_QUERY_COUNT`, `SOV_TRACKING_SET_N_DEFAULT` 코드 미참조. 반대로 `INDEXNOW_KEY/ENABLED`, `COST_GUARD_*_LEADGEN_CALLS`, `REPUTATION_RELEASE_REVISION`(프로덕션 부팅 필수), `CUSTOM_DOMAIN_IP_TARGETS`, `ADMIN_EMAIL/NAME/PASSWORD`는 `.env.example`에 없음.

### 6.3 프론트
- Admin: `lib/report-strategy.ts`(115줄)+테스트 완전 고아(리포트 화면은 `report-review.ts`의 `ReportView` 사용); `types/index.ts` `Report`(442-464), `OperationsIncidentState`(544) 참조 0; `@types/recharts@1.8`이 dependencies(recharts 3.x는 자체 타입); `Dockerfile:22-23`가 `NEXT_PUBLIC_SITE_URL` 미전달 → 번들은 항상 기본값 [L]; `wiki/page.tsx:91` `NEXT_PUBLIC_BACKEND_URL` 분기는 CSP상 사실상 죽은 분기 [L].
- Site: `app/[slug]/_components/HomeEditorialGrid.tsx`(101줄) import 0; `landing-copy.ts`의 `heroScarcity`/`Figure`는 테스트만 참조; `image-policy.ts:37 shouldBypassNextImageOptimization` 미사용; `app/api/leads/route.ts` + `readFormDataBodyWithLimit` 경로 dead.

### 6.4 저장소 파일·문서
| 경로 | 크기 | 판정 |
|---|---|---|
| `standalone/` (landing-v2-2, privacy, terms) | 80KB | 참조 0. Next 이전 정적 시안 |
| `docs/reputation_guide.html` | 48KB | 참조 0, Imagen/GPT-4o 언급 stale |
| `codereview_v1.docx`, `0318refiner.md`, `docs/prd/TEAM1~3-*.md` | 40KB | 참조 0, 역사 자료 |
| `design-qa.md` | | 개인 절대경로(`/Users/woojinlee/...`) 포함 |
| `artifacts/` 51파일 | 421KB | `DESIGN.md`, `seed_clinic_visual_identity.py`가 문자열 인용. `competitor-2026-07-28/*.py` 17개 연구 스크립트는 리포 밖으로 |
| `docs/plans/` 19파일 | 441KB | 상태 헤더 부재. `2026-06-20 Vercel/Supabase`는 표기 없이 유기 [L] — deploy.sh `DB_CONNECTION_MODE=supabase` 분기·`check_vercel_supabase_deploy.py`·`.env.vercel-supabase.example`·README:80-81과 함께 |
| `backend/app/demo_assets/` | 5.4MB | 사용됨(demo_seed). 단 `Dockerfile:61 COPY app`로 프로덕션 이미지 포함 |
| `PretendardVariable.woff2` | 2MB×2 | backend/assets와 site/app/fonts 중복 추적 |
| `scripts/test_full.sh:55,256` | | 제거된 Perplexity 공급자 잔재 |

---

## 7. 토큰·비용 낭비 (LLM·이미지·SoV)

| # | 항목 | 추정 영향 | 위치 |
|---|---|---|---|
| 1 | Essence 2차 재정이 `review_case` 전체 재전송 | 에스컬레이션 건 입력 2배, refresh당 Haiku ≤10회, Celery `max_retries=2` 곱하면 ≤30회 | `essence_auto_review.py:695-712` |
| 2 | prompt cache 미사용(`cache_control` 0건) | 입력 비용 약 1/3 절감 여지 | `content_engine.py:474-479` |
| 3 | 금지 표현·길이·엔티티 누락·가격 표현 실패 시 full 재생성, 재생성 시 `remediation_findings`로 "글 전체 다시" | 편당 Sonnet ≤6회, 출력 토큰도 2배 | `content_engine.py:496-497, 539-550, 602-624, 744-761, 843-854` |
| 4 | 기존 제목 목록 LIMIT 없이 프롬프트 삽입 | 1년차 병원 입력 1.6~2배 | `tasks.py:1946-1952, 2707-2714`, `content_engine.py:437-439` |
| 5 | 동일 안전 규칙 3중 삽입(시스템 프롬프트 + 철학 `avoid_messages` + 브리프 `avoid_messages`), `medical_ad_risk_rules`·`must_use_messages` 2중 | 호출당 0.3~0.9k tok | `content_engine.py:135-140, 304-309, 358-363`, `content_brief.py:55-56` |
| 6 | V0 재시도 측정 재수행 | 최대 3배(450호출) | `tasks.py:1601-1777` |
| 7 | 프로파일 자동 채우기 최대 54k자를 Sonnet으로, 재시도 필터 없음 [L] | 호출당 수 배 | `hospital_profile_autofill.py:37, 170, 190` |
| 8 | SoV OpenAI Responses 경로 `max_output_tokens` 없음(실측 1.6~3k tok), 판정은 `[:3000]`만 사용; 자사·경쟁사 판정 mini 2회 분리 | 소액 누적 | `sov_engine.py:748-761, 1016, 1112, 1010, 1106` |
| 9 | 유료 SoV 답변 캐시 없음 — 질의 템플릿에 병원명이 없어 동일 지역·과 병원 간 재사용 가능한데 매번 재구매. `fetch_answer`/`judge_mention` 분리는 이미 존재 | 병원 수에 비례 | `sov_engine.py:269-294, 1162-1350` |
| 10 | 리드 답변 캐시 키에 judge 지문 포함 → 판정 프롬프트/모델 변경 시 답변 캐시 전부 무효화 | 과잉 무효화 | `sov_engine.py:574-590`, `lead_query_cache.py:57-64` |
| 11 | Admin `POST /sources/{id}/process`가 PROCESSED·`content_hash` 확인 없이 재추출 [L] | 재클릭 시 중복 과금 | `essence.py:625-660`(워커 경로 `tasks.py:1044`는 조기 반환) |
| 12 | `reconcile_essence_snapshots`: COST_BLOCKED 병원을 15분마다 재큐 → cost guard 거부 + incident dedupe 쓰기, 백오프 없음 | 병원당 96회/일 | `tasks.py:1379-1394, 1224-1247` |
| 13 | Google 이미지 IMAGE_SAFETY(결정적) 3회 재시도 후 폴백 3회 | 1장 ≤6회 | `image_engine.py:252-261, 357` |
| 14 | 클라이언트 생명주기: `essence_engine._anthropic_client()` Retrying 루프 시도마다 신규, `content_ai_review.py:202-206` 호출마다, `image_engine.py:332, 368-373` 시도마다 OpenAI/GenAI 생성(Vertex 토큰 재획득) | TCP/TLS 반복 | |
| 15 | 재시도 필터: content/essence/autofill/image-google이 4xx(`BadRequest/Authentication/PermissionDenied`)도 재시도, 파싱 실패 시 수리 프롬프트 없이 full 재전송 | | `content_engine.py:406-416`, `essence_engine.py:384-388` |
| 16 | 판정 클라이언트 `max_retries` 미지정(SDK 기본 2) → `record_provider_call` 과소 계상 [L] | 계측 | `sov_engine.py:124, 1009` |

정상 확인: 모델 하드코딩 0건, `OPENAI_MODEL_PARSE`=mini 준수, essence는 `output_config.json_schema` 사용, 이미지 존재 시 재호출 없음(`tasks.py:403-404`), essence 합성 UP_TO_DATE 게이트, cost_guard 예약이 워커 경로 전부에 존재, 필터 없이 공개되는 생성 경로 없음. 단 essence 스크리닝은 마크다운 비인식 `check_forbidden`(`essence_engine.py:1350-1362`)을 써 생성 검증·발행 게이트(`check_forbidden_content_fields`)와 기준 불일치.

---

## 8. 구조 비효율

### 8.1 DB 쿼리
- `get_sov_queries`(`sov.py:133-190`): 병원의 **모든** `SovRecord`를 `raw_response`(AI 응답 원문) 포함 로드 후 파이썬 그룹핑. 대시보드가 매번 호출. 운영 개월수에 선형 악화.
- `get_sov_trend`(`sov.py:77-130`): 12주 전 행 로드 후 주차별 파이썬 필터(`GROUP BY date_trunc` 1문이면 충분).
- 인시던트 큐(`operations_center_incident_queries.py:114-133`): 5-테이블 조인 `.all()` → 그룹핑 → `grouped[start:start+page_size]`, `total=len(grouped)`. 다른 3개 큐는 offset/limit 사용. overview가 4큐 순회, FE 12초 폴링.
- N+1: `list_reports`(리포트당 4~5쿼리, LIMIT 없음), `list_handoffs`(행당 `db.get`×4), `adjust_query_priorities`(병원×(질의+타깃) SELECT, 전역 600s).
- LIMIT 없는 목록: `list_reports`, `list_sources`, `list_philosophies`, `list_handoffs`, `list_admin_accounts`, `get_sov_queries`, public `list_hospitals`.
- 요청당 반복: public `/contents*` 3개 경로 모두 `_get_active_hospital` + `get_essence_readiness`(소스 자산 전체 로드) — "현재 승인 철학 id"만 필요.

### 8.2 복구 루프·폴링 (유휴 기준)
| beat | 주기 | 실행/h | tick당 DB | DB문/h |
|---|---|---|---|---|
| drain-lead-diagnoses | 1분 | 60 | UPDATE×2, SELECT×5, DELETE, commit×2 | ~600 |
| dispatch-notification-outbox | 1분 | 60 | 임대 + 상관 서브쿼리×2 + httpx 신규 클라이언트 | ≥180 |
| reconcile-monthly-artifact-incidents | 1분 | 60 | 4중 조인 + NOT EXISTS + JSONB 풀스캔 — **월 1회 이벤트용** | 60~120 |
| reconcile-autonomous-workflows | 1분 | 60 | `FOR UPDATE SKIP LOCKED`×3 + commit | ~240 |
| canary ×6 | 5분 | 72 | `SELECT 1` + Redis 신규 연결/PING/SETEX | 72 |
| reconcile-essence-snapshots | 15분 | 4 | COUNT + 200건 + 병원당 ≤4 | 8+8~16·N |
합계 ≈ 256 태스크/h(≈6,150/일), DB ≈ 1,100~1,300문/h + 병원 비례. "유실 publish 복구"가 `autonomous_recovery`·`drain_lead_diagnoses`·outbox 3곳으로 나뉘어 있고, 월간 산출물 인시던트는 태스크 본문·매분 reconcile·6시간 rebuild 3중.

Admin 폴링: 온보딩 `setInterval(refresh, 5000)`이 8요청(`onboarding/page.tsx:2324, 395-410`) = **96 req/min**; 운영센터 12초×2 + run 상세 3초 = 30 req/min; leads·reports 5초. 클라이언트 캐시/dedup 계층 없음(SWR/React Query 없음). 레이아웃+페이지 이중 fetch(profile `:370`, onboarding `:405` — `useHospitalHeader().hospital`을 두고 재호출). content 페이지는 단건 액션 후 월 전체 목록 재조회 9곳(`:463…794`). `GET /admin/handoffs` 전체를 받아 1건 필터(`onboarding:409`).

### 8.3 중복·계층 역전
- 병원 404 헬퍼 16중(`_get_hospital_or_404`×8, `_get_or_404`×3, `_get_hospital` 등) vs 미사용 `api/deps.py`.
- `_enum_value`×4(+`services/enum_values.enum_value` 존재), `_display_label`×4, `_has_public_site`×3, `_configured_custom_domain_ips`×2, `_read_artifact`×2, `_serialize_item`×2, `_run_async`×3(tasks/lead/naver_sync), operation_run_id 파서×5, `_has_valid_doctor_artifact`≈`_artifact_is_valid`(+SQL 버전, 3중), `_check_custom_domain_https`(워커)와 `services/domain_live_status.py`.
- LLM 유틸: JSON 파서 3중(`content_engine:647`, `essence_engine:946`, `content_ai_review:135`), `hospital_profile_autofill.py:25`가 `content_engine._parse_json_response` private import(클라이언트 생성까지 끌어옴), 프로파일 직렬화 3중, `_string_list`/`_short`/`_status_value`/`_iter_text` 2중.
- 라우터에 사는 도메인 로직: `essence.py:1631 _rescreen_content_items` ≡ `essence_auto_review.py:744`; `essence.py:152-345` 사진 provenance 12함수; `hospitals.py:334-460,1153-1358` 준비도 ~200줄 vs `services/hospital_lifecycle.py`.
- 워커 → API import: `workers/milestone_monthly_facts.py:11-15`(`api.admin.reports._artifact_state` 등 private 3개), `tasks.py:4067`(`api.admin.query_targets.seed_query_targets_from_matrix`).
- 프론트: 엔티티 타입 페이지 로컬 재선언 9곳(`Hospital`·`Source`·`Readiness`×3·`AdminAccount`×2), 날짜 포맷터 인라인 12곳(+`onboarding-artifacts.ts:84` 재구현), 라벨 맵 3중(증거노트 타입 문구 상이, 요금제 라벨 3벌, 콘텐츠 유형 '시술 안내'≠'치료 안내'), `SummaryPill` 2벌, `jsonNoStore`/`backendUrlOrNull` 4벌, `login-proxy.ts`≈`admin-proxy.ts`. 컴포넌트 디렉터리 `app/components`와 `app/_components` 공존. MedicalClinic JSON-LD 노드가 `page.tsx:155-204`와 `visit/page.tsx:73-101`에 인라인 중복(정규식 소스 테스트로 정합 강제).
- 거대 파일: `tasks.py` 5,785 / `essence.py` 1,992 / `hospitals.py` 1,505 / `sov_engine.py` 1,424 / `onboarding/page.tsx` 2,577 / `content/page.tsx` 1,876. 모든 admin 데이터 페칭이 클라이언트 `useEffect`(서버 컴포넌트는 redirect 2개뿐), `eslint` `react-hooks/set-state-in-effect` 전역 off.

### 8.4 시간 제한·팬아웃
- 모놀리식: `nightly_content_generation`(50 cap, 3000s — 항목당 60s에 최대 14회 외부 호출), `run_monthly_reports`(2400s, 병원별 태스크 있으나 미팬아웃), `adjust_query_priorities`·`monitor_live_custom_domains`(전역 600s, 순차 HTTP 10s).
- 팬아웃 O: 주간/월간 SoV, 리드, V0, 인증서.

---

## 9. 보안·트랜잭션
- 의존성 체인(`verify_admin_rate_limit → verify_admin_key → capture_admin_actor`) 17개 admin include 전부 적용, 우회 없음. Public은 의도적 무인증 + rate limit. 단 `GET /public/diagnosis/{token}`, `/status`, `/slots`는 데코레이터 없이 기본 60/min만이며 `_resolve_token`이 조회마다 `access_count` UPDATE+commit(`diagnosis.py:207-209`) → 폴링이 쓰기 부하.
- 계정 게이트 이중 기준(§2-8): `require_*_account` 51참조/약 38엔드포인트, 나머지 write는 키만. `ADMIN_REJECT_UNVERIFIED_ACTOR=True`는 **위조 헤더**만 막고 **헤더 부재**는 통과(`security.py:143-145` 주석 "배치/시스템 호출").
- 커밋 전 큐잉 없음(`hospitals.py:856→862`, `content.py:312→329`, `essence.py:733→737`, `diagnosis.py:495→513`). 두 번 커밋 패턴(`domain_verification.py:160→207`, `content.py:730`)은 설계상 허용으로 보이나 미문서.
- 시크릿 가드: 빈 값 fail-fast는 있으나 `ADMIN_SECRET_KEY` placeholder(`REPLACE_ME/change-me`) 통과; `docker-entrypoint.sh:27` flower 기본 비밀번호 `changeme`.

---

## 10. 인프라·설정·툴링
- 서비스 5개 + Job 3개 중 `reputation-redbeat-reconcile`, `reputation-production-readiness`는 terraform 미정의(deploy.sh ad hoc). terraform `image/env`는 `ignore_changes`로 deploy.sh에 위임, deploy.sh는 env 전량 교체 + `require_no_dropped_terraform_env` 가드 — 두 소스 공존, "어디를 고치나"가 변수마다 다름.
- 헬스체크 경로(`/health/ready|live`, worker `/ready|/live` 8080, LB `/api/v1/health/ready`) 모두 정합. `backend/Dockerfile:75-77` 주석 "worker/beat expose no HTTP server"는 stale.
- 마이그레이션: head 단일 `0060`, 최근 리비전 모델 반영 확인. 결함은 §2-2. 데이터 마이그레이션 내장 7건, migrate Job `--task-timeout=300 --max-retries=0`. `scripts/test_deploy_runtime.py:18-31`은 0031을 "deployed head"라 부름 [L].
- 의존성: `google-cloud-aiplatform`·`python-dotenv`·`httpx2`(dev) 직접 import 0; 전이 의존성 직접 import(미선언): `anyio`(6파일), `kombu`, `billiard`, `limits`. site `react ^18` vs admin `react 19.2.4`(같은 Next 16).
- `pytest.ini`(루트)와 `pyproject [tool.pytest]` 이중. 통합 테스트는 `INTEGRATION_DATABASE_URL` 미설정 시 조용히 skip.

---

## 11. 테스트 위생
- **소스 텍스트를 grep하는 테스트**: admin 11개(`admin-ux-r1`, `fable5-ui-contract`, `onboarding-copy/-page-locks/-photo-public/-visual-step`, `revisit-design-tokens`(전체 app 스캔), `schedule-page-r2`, `essence-evidence-wiring`, `account-operations`), site 19개 파일(`fable5-ui-contract`, `image-delivery`, `article-body-links`, `clinic-entity-schema`, `clinic-section-header`, `diagnosis-proxy-paths` 등). 리팩터마다 깨지고 동작은 보증 못 함. 공통 원인: async 서버 컴포넌트를 렌더할 러너 부재(`node --test`, lib만).
- dead code를 살려두는 테스트: `test_notifier.py:142-213`, `test_notifier_ops_contract.py`, `test_onboarding_notifications.py` 등 6개 파일(백엔드), `report-strategy.test.ts`(admin), `landing-copy.test.ts`의 `heroScarcity` assert(site).
- `landing-copy.test.ts:544-584` 문체 린트 4개("아니라 ≤2회", "부정문 비율 <30%"). 나머지 금지표현·성과보장·수치출처 검사는 가치 있음.
- CSS 캐스케이드 테스트 4종(`clinic-tap-target` 등)은 행위 근사로 유지 가치 있으나 자체 specificity 계산기를 테스트로 재검증(`tap-target:144`).
- `test_tasks_nightly.py:757-761`이 "COST_BLOCKED 슬롯 30회 실행 → writer 1회"를 고정 → §2-7의 영구 스킵이 의도된 설계로 굳어 있음.

---

## 12. 정리(삭제·이동) 후보 목록

**삭제 가능 (참조 0, 근거 확인)**
- 백엔드 함수: `notifier.py` 미호출 22개(V0/허브/자동발행/월간 4개 포함), `tasks.py:777-812` Redis 멱등 블록, `_query_gemini`, `QUERY_TEMPLATES`, `get_source_text`, `enqueue_onboarding_event`, `enqueue_monthly_event`, `_ensure_brief_capable_exposure_action`, `_needs_attention_clause`, `safe_task_id`, `is_private_asset_ref`, `_medical_risk_rules`, `publication_text`, `ensure_monthly_artifact_failure`, `missing_profile_requirement_labels`, `submit_hospital_pages`, `_enqueue_overdue_post_publish_review_notifications` 스텁, `operations_center.py` `__all__`, `HandoffPendingCreate`
- 날짜 만료: `nowon_august_backfill.py`, `nowon_orthopedic_faq_regenerate.py`, `tasks.py:4203-4210`, 2026-08 특례 2곳
- 엔드포인트: `generation-claim/release`, `correct-contract`, incident `assign`×2 (UI 도입 전까지)
- 프론트: `admin/lib/report-strategy.ts`+test, `types/index.ts` `Report`·`OperationsIncidentState`, `@types/recharts`, `ALLOWED_PREFIXES` 5개, `site/.../HomeEditorialGrid.tsx`, `heroScarcity`/`Figure`, `shouldBypassNextImageOptimization`, `site/app/api/leads/route.ts`(+백엔드 `/public/leads` 유지 여부 결정)
- 파일: `standalone/`, `docs/reputation_guide.html`, `scripts/test_full.sh` Perplexity 잔재
- 설정: `GCP_LOCATION`, `LEADGEN_QUERY_COUNT`, `SOV_TRACKING_SET_N_DEFAULT`

**이동·결정 필요**
- `codereview_v1.docx`, `0318refiner.md`, `docs/prd/TEAM*`, `design-qa.md` → `docs/archive/`
- `artifacts/competitor-2026-07-28/*.py` → 별도 저장소; `artifacts/` 나머지 → `docs/audits/`
- `demo_assets/` → `.dockerignore` 또는 GCS
- Vercel/Supabase 경로(deploy.sh 분기, `check_vercel_supabase_deploy.py`, `.env.vercel-supabase.example`, README, plan 06-20) 유지/폐기 결정
- `ops_control_qa_seed` → Makefile 타깃 추가 또는 삭제; `fix_director_credential`·`seed_colon_cluster` entrypoint 제거
- 컬럼 `aeo_site_path`, `exposure_gaps.diagnosed_at`, `manifests.frozen_at`, enum `PLAN_8` → drop 마이그레이션

---

## 13. 권고 로드맵

**즉시 (1주 내, 리스크·비용 직결)**
1. 비공개 리밸리데이션 복구: `PUBLISHED` 조건을 `published_at is not None`으로 완화하거나 reject 전용 run 키. 또는 태그 기반 `revalidateTag('hospital:{slug}')`.
2. 0053/0054 드리프트: 모델 추가 또는 0061 drop, 0054 테넌트 UPDATE를 seed로 이전, CI에 `alembic check`.
3. V0 측정 체크포인트: `MeasurementRun` COMPLETED + SovRecord 존재 시 재시도에서 측정 스킵, 또는 PDF/커밋 단계를 별도 태스크로.
4. cost_guard 우회 3곳에 `check_and_increment` 추가.
5. `.env.production.example`·`.env.example` 값을 config 기본값과 맞추고 `test_deploy_runtime.py:127`에 "템플릿=기본값" 검사 추가. 누락 env 5종 추가.
6. compose: entrypoint에 `"$@"` 분기 또는 `command:` 삭제; `make test`를 dev 타깃으로. CI를 `uv sync --locked --extra dev`로.
7. 미라우팅 태스크 5개를 `task_routes`에 추가 + `test_celery_routing.py`에 "registered ⊂ routed" 검사. `backfill_indexnow`는 Job으로 이관 또는 삭제.

**2~4주 (비용·부하)**
8. Anthropic prompt cache: `system`을 [SYSTEM_PROMPT + source hint + 병원 철학 컨텍스트] 블록 배열로, `cache_control` 2브레이크포인트.
9. Essence 재정 페이로드를 blocking_findings 참조 노트만으로 축소(또는 1차 대화에 이어붙여 prefix 캐시).
10. 저비용 수리 경로: 금지 표현·엔티티 누락·가격 표현은 Haiku "해당 문장만 재작성" 1회 후 실패 시 full 재생성. 기존 제목 목록 LIMIT/기간. 안전 규칙 3중 삽입 제거(`FORBIDDEN_EXPRESSIONS` 단일 소스 렌더).
11. 지문 억제에서 `COST_BLOCKED/PROVIDER_TIMEOUT/PROVIDER_UNAVAILABLE` 제외 + 소규모 재시도 상한; nightly에 `SoftTimeLimitExceeded` 별도 처리; `reconcile_essence_snapshots` COST_BLOCKED 백오프.
12. 복구 루프: `reconcile-monthly-artifact-incidents` 1분→15분(또는 커밋 후 1회 트리거), outbox 내부 재조정 5~15분, canary Redis 연결 재사용, `task_ignore_result=True` 전역 + opt-in. `run_monthly_reports`를 dispatcher로 팬아웃, PARTIAL 재빌드 하루 1회, 배치 `autoretry_for` 제거.
13. DB: `get_sov_queries/trend` SQL 집계(`raw_response` 제외), 인시던트 큐 offset/limit + count, `list_reports/handoffs` 배치 로드 + limit, public `/contents*`·이미지 프록시에서 `get_essence_readiness` 대신 승인 철학 id 단일 쿼리, 목록 응답에 `treatments` 포함.
14. Admin: 온보딩 폴링을 `sources` 단일 호출로(96→12 req/min), 레이아웃 컨텍스트 재사용, 단건 액션 후 로컬 갱신, `handoffs`에 `hospital_id` 필터. BFF에 `X-BFF-Auth/X-Visitor-IP` 전달 또는 rate-limit 키를 검증된 actor로.
15. Site: 콘텐츠 목록 캐시 키 1개(500)로 통일 후 slice, `/[slug]/contents` 필터를 세그먼트/클라이언트로 옮겨 ISR 복귀, `revalidate` 선언 정합, essence 경로에 `treatments` 전달, 진단 GET 3개 rate limit.

**분기 (구조)**
16. 계정 게이트 정책 결정: 모든 admin write에 `require_active_account` 라우터 레벨 적용, 시스템 호출은 별도 서비스 토큰.
17. 공용 계층: `api/deps.get_hospital_or_404` 채택 + 16개 복제 제거; `enum_value/display_label/has_public_site` services 승격; `services/llm_common.py`(JSON 파서·프로파일 직렬화·클라이언트 싱글턴·재시도 필터); `_run_async`·run_id 파서 단일화; 워커→API import 제거(`report_delivery_gate`, `query_target_seed` 서비스화); `essence.py` 사진 provenance·`_rescreen`·`_get_approved`를 서비스로 통일.
18. 승인 신선도 단일 규칙: 생성·발행·`/philosophy/approved` 모두 `get_current_approved_philosophy` 사용(CLAUDE.md STEP6과 일치) 또는 문서를 코드에 맞춰 개정.
19. `tasks.py` 도메인별 분할(content/sov/report/site/lead), admin 거대 페이지 분할 — 선행조건으로 소스 grep 테스트 30개를 행동 테스트로 전환(vitest + `react-dom/server` 또는 Playwright 스모크).
20. `MonthlyReport` 정본 정리(artifact/event에서 파생), `SovRecord` legacy `QueryMatrix` 조인 → `AIQueryTarget`으로 완주, `PLAN_8`·고아 컬럼 drop.
21. CLAUDE.md 전면 개정: 스택·구조·모델·beat 전체·Slack 정책 링크(`docs/ops/slack-notification-policy.md`)·금지 표현 SoT(`medical_filter.py`)·분량 규칙·stale 승인 규칙·24일 이후 주간 측정 규칙·env는 `.env.example` 위임. IaC 경계(Job 2개), Vercel 경로, `--check` 모드 주석도 함께.

---

## 부록 A. 기계 검사 결과 요약
- `ruff check app --select F401,F811,F841,F821`: 0건 (import 위생 양호).
- `vulture --min-confidence 100`: 4건(`essence.py:154 is_public_form`, `observability.py:99 hint`, `admin_session_revocation.py:20 ex`, `essence_engine.py:1166 require_text_support`).
- `settings.*` 참조 대조: 정의 95 / 사용 88. 미사용 3(+DB URL 조립용 4).
- 라우트 트리: 144 엔드포인트, 정규화 중복 0. Alembic: head 1, base 1, 병합 0.
- 모듈 import 그래프: 앱 코드에서 import되지 않는 모듈은 진입점(main, 라우터, utils 스크립트)뿐. 프론트 고아 모듈 3개(§6.3).

## 부록 B. 정상으로 확인된 핵심 배선 (재확인 불필요)
- 프론트→백엔드 호출 깨짐 0, 라우터 include 누락 0, beat↔routes↔소비 큐 일치, 커밋 전 큐잉 없음, 공개 표면 승인 데이터 게이트(ACTIVE+site_live, PUBLISHED+ALIGNED+현재 승인 철학), JSON-LD/FAQ/sitemap/robots/llms.txt 동일 소스, 호스트 라우팅 캐시, nightly 멱등(claim+SKIP LOCKED+지문+이미지 재호출 방지), SoV cell 단위 커밋, 인증 TTL > 최대 countdown, 모델 하드코딩 0, 필터 없이 공개되는 생성 경로 0.
