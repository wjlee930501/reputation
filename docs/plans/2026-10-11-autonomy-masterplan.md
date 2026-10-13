# 자율 운영 보완 MasterPlan (2026-10-11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 이 문서는 인덱스·순서·결정·검증 기준이고, 각 태스크의 단계별 코드는 아래 서브 계획 6개에 있다.

**Goal:** Re:putation을 "사람이 매일 배포하고 알림을 읽어야 굴러가는 반자동 시스템"에서 "정상 경로·실패 복구·자기 감시가 스스로 수렴하고, 사람은 계약·공식 정보·공개 주소·진짜 예외만 맡는 시스템"으로 바꾼다.

**Architecture:** 감사([2026-10-11 감사·개선 계획](./2026-10-11-autonomy-audit-and-improvement-plan.md))가 확인한 구조 결함 F1~F12를 세 Phase로 닫는다. Phase 0은 **수렴하지 않는 재시도·재배달**을 수렴시키고(생성 예산·재시도 정책·디스패치), Phase 1은 **사고 모델을 "사람의 일"에 맞추며**(유형 소유 레지스트리·해결 규칙·월초 오탐·V0/리드 자동 재실행), Phase 2는 **온보딩 정체·자기 감시·배포**를 코드화한다. 의료·사실·tenant·공개 권한·승인 근거·artifact 무결성 검사는 어디서도 완화하지 않는다.

**Tech Stack:** Python 3.11/FastAPI/SQLAlchemy/Alembic/Celery/RedBeat(backend), pytest(단위는 DB 없이, `*_postgres.py`는 로컬 Postgres×3+Redis), bash(`scripts/deploy.sh` + `scripts/test_deploy_runtime.py`), Terraform(GCP 경보). 기준 소스 main `8c8f6e60`, DB head `0086`.

---

## 1. 서브 계획과 실행 순서

| 순서 | 서브 계획 | 태스크 | 핵심 결함 | 파일 겹침 |
|---|---|---|---|---|
| P0-A | [Phase 0 생성 예산](2026-10-11-p0-generation-budget.md) | 0-1, 0-2 (+결정 D1 반영 태스크 0-1b) — **완료**: 0-1 `791263b0`, 0-1b `c43f4486`, 0-2 `9ef74442`, 후속 `fd547c2a` | F1, F2 | `generation_attempt_state.py`, `tasks.py` |
| P0-B | [Phase 0 재시도 정책](2026-10-11-p0-retry-policy.md) | 0-3, 0-4, 0-5, 0-6 — **완료**: 0-3a `b14b5da4`, 0-3b `1cf59df7`, 0-4 `162c1659`, 0-5 `6c659ac5`·`ed84c14a`, 0-6a `ba358a13`, 0-6b `779d6beb`, 후속 `557694f5` | F9, 교체 실패, INPUT_CHANGE 동결 | `generation_retry_policy.py`, `generation_run_control.py`, `topic_swap_fallback.py`, `tasks.py` |
| P0-C | [Phase 0 디스패치·복구](2026-10-11-p0-dispatch-recovery.md) | 0-7, 0-8a/b/c, 0-9a/b, 0-D(문서) — **완료**: 0-7 `a0096698`, 0-8a `7ec21e0f`, 0-8b `48e2a4c5`, 0-8c `a9d7f9a3`, 0-9a `e8cad029`, 0-9b `07063d86`, 후속 `c991efc4`, 운영 조회 CLI `16b9e30c`·`81d6b0c2`, 병합 `ab35c1f9`, 0-D는 Phase 0 문서 커밋 | F8 | `dispatch_envelope.py`, `autonomous_recovery.py`, 마이그레이션 **0087** |
| P1-A | [Phase 1 사고 레지스트리](2026-10-11-p1-incident-registry.md) | 1-1a/b/c, 1-2, 1-3, 1-4, 1-5, 1-8, 1-10b/c | F3, F4, F10(일부) | `incident_types.py`, `incidents.py`, `incident_backlog.py`, `notification_store.py`, `autonomous_recovery.py` |
| P1-B | [Phase 1 월간·리드](2026-10-11-p1-monthly-leads.md) | 1-6a/b/c, 1-7a/b, 1-9a/b/c-1/c-2 | F5, F6, F12 | `tasks.py`, `weekly_sov_incident_control.py`, `monthly_report_gap_notifications.py`, `lead_diagnosis_tasks.py`, RedBeat 버전 |
| P2-A | [Phase 2 온보딩](2026-10-11-p2-onboarding.md) | 2-3a~d, 2-1a~c(+D3 안분 태스크 2-1d), 2-2a~c, 2-4a/b/(c 플래그), 2-5a/b | F7 | `essence_auto_review.py`, `content.py`, 새 모듈 2개, `tasks.py` |
| P2-B | [Phase 2 인프라·배포](2026-10-11-p2-infra-deploy.md) | 2-6a~d, 2-7, 2-8a/b, 2-9-0~e, 2-10(개요) | F8, F11 | `scripts/deploy.sh`, `terraform/*.tf`, `health_server.py`, `cost_guard.py` |
| P3 | [Phase 3 콘텐츠 게이트](2026-10-13-p3-content-gates.md) (2026-10-13 추가, 브랜치 `claude/autonomy-p3`) | 3-1~3-15 (D23·D24, 엔지니어링 기본값 E1~E5) | 게이트 공회전·필터 오탐·진료비 참고자료 보류 | `utils/medical_filter.py`, `utils/forbidden_impact_scan.py`, `services/content_engine.py`, `services/reference_*.py`, `workers/tasks.py`(게이트 카탈로그 판 `2026-10-13.1`) |

**실행 규칙**
- Phase 안에서 파일이 겹치는 서브 계획은 **직렬**(P0-A → P0-B), 겹치지 않는 것은 **병렬 worktree**(P0-C는 P0-A/B와 병렬). Phase 1은 P1-A → P1-B 직렬(둘 다 `tasks.py`·사고 서비스). Phase 2는 P2-A ∥ P2-B 병렬.
- Phase마다 브랜치 하나(`claude/autonomy-p0`, `-p1`, `-p2`) → PR → Codex(GPT) 총검토 → 머지 → **하루 1회 배포**(배치 시간대 23·01·04·07·12·18·22시 KST 밖). 이전 Phase가 배포돼 하루 관측된 뒤 다음 Phase를 배포한다.
- 구현은 Opus 5.5(high), 검토·검수는 Fable 5.1. 태스크마다 fresh 서브에이전트, 태스크 완료마다 스펙 준수 검토 + 코드 품질 검토(superpowers:subagent-driven-development).
- 마이그레이션 번호: P0-C가 `0087`을 쓴다. 이후 Phase에 마이그레이션이 생기면 `0088`부터 체인을 잇는다(P1·P2 초안은 마이그레이션 없음).
- RedBeat 버전(`REDBEAT_SCHEDULE_VERSION`)은 Phase 1에서 한 번만 올린다(P1-B가 올림. P1-A는 Beat 항목을 바꾸지 않는다).
- 각 서브 계획의 "계약 영향" 문장은 그 Phase의 마지막 태스크에서 CLAUDE.md에 한꺼번에 반영한다(문서 버전·갱신일 포함).

## 2. 결정 사항 (2026-10-11 확정)

| # | 결정 | 반영 |
|---|---|---|
| D1 | 주제당 작가 예산(6회)이 소진되면 **3일을 기다리지 않고 즉시 주제 교체 후보**로 본다 (상한 18회 상향은 하지 않음) | P0-A에 태스크 **0-1b** 추가: `topic_swap_fallback.exhausted_body_sample_reason`이 현재 주제의 `GENERATION_BUDGET_EXHAUSTED`를 소진 일수와 무관하게 교체 사유로 인정 |
| D2 | 공개 글 사후검수의 교정 실패 HARD는 **Slack·운영자 일로 올리지 않는다**(10/8 지시 유지) | P1-A의 **1-10a 제외**. 1-10b(UNCERTAIN만 남은 글의 상위 모델 재검수·자동 닫기)·1-10c(`AUTO_PUBLISH_HOLD_HOSPITALS` readiness·일일 요약 노출)는 진행 |
| D3 | Essence 자동 승인 시 첫 일정은 **이번 달 남은 기간을 안분**해 바로 시작 | P2-A에 태스크 **2-1d** 추가: 달력 엔진의 엄격 용량 검사에 "첫 달 비례 배분" 규칙(남은 게시 가능 요일 ÷ 월 전체 게시 요일 × 계약 편수, 올림, 최소 1)과 계약 이행 집계의 첫 달 안분 정의 |
| D4 | **Phase별 배포**(하루 1회) | 위 실행 규칙 |
| D5 (기본값) | `OPERATION_RECOVERY_EXHAUSTED`는 개발 대상, 자동 닫힘 규칙은 1-2에 포함(같은 원인 코드의 FAILED 실행이 모두 성공한 후속 실행을 가지면 RECOVERED) | P0-C, P1-A |
| D6 (기본값) | `COST_GUARD_*`는 **운영 대상 유지**(비용 한도는 대표 결정), `UNVERIFIED_ADMIN_ACTOR`·`MONTHLY_SOV_COHORT_OVER_LIMIT`·소진 후 `SITE_BUILD_*`는 개발 대상 | P1-A 1-8 |
| D7 (기본값) | `SOURCE_PROCESSING_FAILED`는 강등하지 않음(유일한 신호) | P1-A 1-8 |
| D8 (기본값) | 월 SoV 경고선은 `COST_GUARD_MONTHLY_SOV_QUERIES // 150`(=28곳)로 계산, 설정 필드 삭제 | P1-B 1-6c |
| D9 (기본값) | V0 완료 측정 재사용 창은 같은 OperationRun lineage 한정 **7일** | P1-B 1-9a |
| D10 (기본값) | 매니페스트 없는 달은 "이번 달은 측정이 없어 전달 불가, 다음 달부터 자동"으로 AE에게 안내(원장 안내 문구는 AE 재량) | P1-B 1-7a |
| D11 (기본값) | 플래그 기본 꺼짐: 활성화 게이트 축소(2-4c), 근거 0건+fetch 전부 실패 사고(2-2d), 기본 주소 선공개(2-5 대안은 태스크 아님) | P2-A |
| D12 (기본값) | 자기 감시 2차 경로는 **GCP 로그 경보(2-6b) + 앱 메일(2-6c/d)** 둘 다. 수신 주소는 Terraform `alert_email`과 같은 값을 `OPS_ALERT_EMAIL` 기본으로 | P2-B |
| D14 (기본값) | 진료비·병원 선택 참고자료 보류의 주제 교체 편입은 `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES=False`(기본 꺼짐, CLAUDE.md "곧바로 OPERATOR_REQUIRED" 계약 유지). 인용 없는 모델 HARD를 주제 교체 계단으로 보내는 0-6a는 플래그 없이 진행하고 "INPUT_CHANGE_REQUIRED로 남긴다" 문장을 고친다. 환경 장애 예산은 일일 4회 초기화 + 3일 상한(`environment_since`), 지수 백오프는 넣지 않음(스윕 간격이 그 역할) | P0-B |
| D13 (기본값) | 하루 1회 배포 잠금은 Worker·Beat를 바꾸는 대상에만. health `release`는 커밋 SHA 대신 `REPUTATION_RELEASE_REVISION`(이미 노출 중인 값)만. 자동 롤백은 `AUTO_ROLLBACK=0` 스위치로 끌 수 있게. 2-10 CD는 사람 승인 클릭 유지 | P2-B |
| D15 (구현 중 확정) | 움직임 없는 RUNNING 잔재 판정은 **10일**(`updated_at` 무변화 + lease 없음/만료), 월간 측정 구간 실행 `MONTHLY_SOV_PERIOD`만 **20일**(말일 마지막 창 뒤 다음 달 8일 최종화까지 약 7일 6시간 무갱신이 정상). §3 0-9의 '7일'을 대체한다 | P0-C 0-9b (`autonomous_recovery._ABANDONED_RUNNING_AFTER(_BY_TYPE)`) |
| D16 (구현 중 확정) | `environment_since`가 없는 레거시 환경 장애 기록은 3일 창의 시작을 마지막 관측일(`observed_at`)로 본다(`first_observed_at`은 옛 표본 실패 시각을 물려받아 이미 닫힌 창으로 읽힌다) | P0-B 0-3a 후속 `557694f5` |
| D17 (수용) | 3일짜리 공급자 장애가 주제당 본문 전송 상한(36)을 다 써 생성 예산 소진이 되면 한 번뿐인 주제 교체를 쓸 수 있다 — 전송 예산이 없는 주제는 교체로만 이어갈 수 있으므로 받아들인다 | P0-A 0-1b, `topic_swap_fallback.exhausted_body_sample_reason` |
| D18 (구현 중 확정) | `MONTHLY_SOV_MEASUREMENT_FAILED`의 RETRYING 창은 복구 마감(다음 달 7일 23:59:59)이 아니라 **다음 달 8일 09:00 KST**(`monthly_period.monthly_final_judgment_at`)에서 끝난다 — 00:00~00:15 사이에 보류된 '조치 필요'가 나가고 곧 8일 00:15 최종화가 닫는 오탐을 막는다. 공백 요약의 '자동 복구 종료' 판정과 같은 시각 | P1-B 1-6a `24825750`, 1-6b `fd5ec07b` |
| D19 (구현 중 확정) | V0 자동 재실행의 사고 기한 유예(`V0_AUTO_RERUN_GRACE`)는 **6시간**(초안 2시간). 재실행이 비용 창 때문에 다시 미뤄지면 RETRYING 사고 기한을 미룸 시각 + 6시간으로 민다(`ops_incident_alerts.extend_retrying_deadline`, 사고가 없으면 만들지 않음). 재실행 1회 = 시도 1회(저장된 실패 횟수가 이미 상한이라 첫 예외에 최종 실패 경로로 간다) | P1-B 1-9a `32df68af`, 후속 `5a1ccdde` |
| D20 (구현 중 확정) | `SITE_BUILD_RETRIES_EXHAUSTED`(하루 자동 재실행 예산 소진)는 **개발 대상**이며 알림 문구가 운영센터의 연결 작업 '작업 다시 시도' 버튼 경로를 말한다. 사람이 누른 재시도의 실패(`SITE_BUILD_FAILED`)는 운영 대상 그대로 | P1-A 1-8 `34e87398`, 후속 `4ecf8e71` |
| D21 (수용) | 주간 측정 실패(`WEEKLY_SOV_MEASUREMENT_FAILED`)를 조용한 상태로 바꾸면서, 같은 사고에 묶인 2026-09 주간 측정 기준표 정책 전환 안내(Report)도 Slack에 가지 않는다 — 사고 행 문구는 남고 사람의 일은 월간 측정 실패·코호트 공백이 따로 연다 | P1-A 1-8 `34e87398` |
| D22 (2026-10-13 대표 결정) | 10/9 이전에 만든 빈 슬롯 **23건**(10/12~15 예정, `LEGACY_SPEND_UNKNOWN` 11 + 게이트가 `CONTENT_NOT_GENERATED`로 덮은 12)의 레거시 예산을 운영센터 버튼과 같은 교체 함수(`generation_attempt_state.replace_legacy_budget_and_release`)를 시스템 actor로 부르는 CLI(`app.utils.legacy_budget_reset`, dry-run 기본)로 **한 번만** 교체한다. 이후의 레거시 슬롯은 버튼으로 처리 | main `8cf31b22`(PR #242), [배포 안내](../ops/deployment-runbook.md) Phase 0 배포 뒤 1회 조치 |
| D23 (2026-10-13 대표 결정) | 진료비·병원 선택 글(제목 기준)은 필수 유형이어도 참고자료가 **선택**이다 — 비어도 생성·발행·공개를 막지 않고, 있으면 같은 실제 문서 검증을 거친다. 비었을 때 수기 목록으로 채우지 않는다(9/29 결정 유지) | P3 3-6, `reference_requirement.references_block_when_empty` |
| D24 (2026-10-13 대표 결정) | 치료 결과 보장, 최저가·가격 최상급, 노골적 할인·이벤트·무료 유인은 공통 필터(`medical_filter`, 라벨 `할인·유인`)로 **하드 차단**한다(생성·발행·공개·PATCH 공통). 그 밖의 문형은 유연하게 두되 의료 안전 차단은 어디서도 풀지 않는다 — 문맥 예외는 같은 문장의 양성 근거를 요구하고, 같은 문장이 결과를 보장하면 어떤 예외도 적용하지 않는다. 어휘 경계는 법무 확인 권장(P3 계획 Q1) | P3 3-1·3-2·3-10, 교차 검토 반영 |

D18~D22는 main에 먼저 기록됐다(D18~D21은 Phase 1 문서 커밋, D22는 PR #242의 배포 안내). 이 브랜치의 기준 커밋(`7a1e2174`)에 없어 같은 문장을 옮겨 둔다.

## 3. 감사 가정을 조사가 바로잡은 것 (서브 계획이 따르는 사실)

- **0-1** "예산 0으로 리셋"은 `_validate_versioned_budget` 불변식을 깨 `LEGACY_SPEND_UNKNOWN`을 만든다 → 교체 시점 획득 위치를 경계(`prior_topic_spend`)로 남기고 그 뒤만 센다. `GENERATION_BUDGET_VERSION`은 올리지 않는다. 본문 전송 예산도 주제별, 이미지 전송은 슬롯 평생.
- **0-2** 재생은 "검수 차단 후보"가 아니라 "본문이 저장되지 않은 실패"에서만 일어난다. 키는 `writer:{ctx}:{topic}:{claim_token}:{fp}`(토큰은 주제 뒤).
- **0-8** QUEUED 장기 체류의 주원인은 큐 우선순위 적체(reports가 content·sov 뒤)다 → 45분 고정 대신 45m→90m→3h→6h 백오프 + 유형별 예산 3회(`operation_runs.redispatch_count`, 0087).
- **0-9** `GENERATE_MONTHLY_REPORT(rebuild)`·`RECERTIFY_PUBLISHED_IMAGE`·`SITE_REVALIDATION`은 인수 제외. 잔재 판정은 `updated_at` 7일 무변화 + lease 없음/만료.
- **1-6a** RETRYING 전환만으론 부족 — 8일 최종화 때 `MONTHLY_SOV_MEASUREMENT_FAILED`를 기계가 닫아야 한다. **1-6c** 운영 env에 `SOV_MONTHLY_COHORT_LIMIT`이 명시돼 있어 기본값 상향은 무효. **1-7** 주간 배치는 이미 SovRecord 없는 ACTIVE 병원을 측정하므로 "기록 없음" 주간 사고는 오탐 → 월말 창 tick에서 1회 측정 + RETRYING(24h). **1-9a** V0 6시간 재사용 창 때문에 재실행마다 150호출 재구매 → lineage 한정 7일. **1-9c** Slack 상태만 PENDING으로 바꿔선 drain이 집지 않음(claim·조회·전송 셋 다 수정); SMS는 연결 실패·429·503만 재시도(이중 발송 방지).
- **1-1/1-6 중복**: `MONTHLY_SOV_MEASUREMENT_FAILED`의 RETRYING 전환은 **P1-B 1-6a가 구현**하고 P1-A 1-1은 레지스트리 항목만 등록한다. **1-3** 기본 기한은 술어가 아니라 RETRYING 진입 시점에 부여(MISSING_APPROVED_ESSENCE의 의도된 무기한 보존). **1-4** 알림 보류는 스윕 소유 유형에만(07:45 요약 보존). **1-5** 자동 닫기는 "사고 마지막 관측 뒤 끝난 연결 실행 SUCCEEDED"로 좁힘, 콘텐츠 생성 사고 제외.
- **2-1** 사람 경로 전체를 서비스로 뽑지 않고 첫 일정 전용 sync 서비스 + 기존 슬롯 생성기 재사용 + 같은 감사행. **2-2** 온보딩 마일스톤 모듈은 Slack 요약 전용이라 사고용 새 모듈. **2-4** `clinic_phone`은 보존 기간 파기를 우회하므로 복사하지 않고 화면 제안만. **2-5** 수동 확인 경로는 `api/admin/domain_verification.py`, 폴링 창은 `connect_domain` 감사행.
- **2-6** 운영 Slack 사망 시 워커 ERROR→이메일 경보는 이미 있다. 실제 공백은 API 쪽 watchdog 전송 실패와 Cloud Scheduler 실패 → GCP 로그 경보 2개. **2-7** Beat `/live`에 Redis 검사를 넣으면 Redis 장애 때 무한 재기동 → RedBeat tick 파일(30초, stale 600, 기동 유예 900). **2-8c** 달러 상한은 cost 컬럼이 없어 보류. **2-9d** health `release`는 Site 리비전이라 API `/api/v1/health/live`의 `REPUTATION_RELEASE_REVISION`으로 smoke. **2-9e** 고정 트래픽 차단은 #238이 이미 함 — `--unpin`은 새 리비전 Ready 뒤 이동만.

## 4. 실행 환경

- 로컬 테스트: `source <scratchpad>/masterplan/testenv.sh` 뒤 `cd backend && .venv/bin/python -m pytest <file> -q`. 컨테이너 `rep-pg-main`(5432)·`rep-pg-aux`(5434, +`reputation_autonomy_migration`)·`rep-pg-redel`(55432)·`rep-redis`(6379)는 2026-10-11에 띄워 head 0086까지 올렸다. 새 마이그레이션(0087)이 생기면 세 DB 모두 `alembic upgrade head`를 다시 한다. `REQUIRE_PDF_RENDER=0`이라 PDF 렌더 테스트는 skip된다 — "전체 통과"로 보고하지 않는다.
- 기준선: Phase 0 시작 전 main `8c8f6e60`에서 `ruff check` + 전체 pytest 결과를 `baseline-tests.log`에 남긴다(실패가 있으면 그 집합을 "기존 실패"로 기록하고 각 태스크는 새 실패 0을 기준으로 한다).
- 운영 DB 조회: 읽기 전용 CLI가 없다. **Phase 0 첫 태스크로 `backend/app/utils/ops_sql_report.py`(Q1~Q6 내장, `SET TRANSACTION READ ONLY`, JSON 출력)를 추가**하고 `gcloud run jobs execute reputation-migrate --args=python,-m,app.utils.ops_sql_report,--only=q1,...`로 돌린다(쿼리 원문: `testenv-and-queries.md` Part B). 배포 전후 지표(§6)는 이 CLI로 뽑는다.
- 터미널: 각 Phase 배포는 [배포 안내](../ops/deployment-runbook.md)대로. 배포 직전 `.env.production`을 운영 API env와 대조한다(P2-B 2-9a가 이 수동 단계를 없앤다).

## 5. Phase별 완료 기준 (Definition of Done)

**Phase 0** — (a) 서브 계획 P0-A/B/C 전 태스크 커밋, (b) 단위 + `*_postgres.py` 전체 새 실패 0, `ruff` 통과, `make copy-guard`·`make db-budget-guard` 통과, (c) 운영 DB 사전 조회: 10일 넘은 RUNNING 잔재 수(D15)·교체된 슬롯 수 기록, (d) Codex 총검토 동의, (e) 배포 뒤 readiness 17개 검사 통과·새 리비전 ERROR 0건, (f) 배포 다음 날 지표: 교체 뒤 첫 시도가 `WRITER_RESULTS_EXHAUSTED`인 슬롯 0, `OPERATION_REDISPATCH_EXHAUSTED` 사고 ≤ 원인 1건, 만료 봉투 ERROR 0.

**Phase 1** — (a)(b)(d)(e) 동일, (c) 사전 조회 Q1(OPEN 나이 분포)·Q2(알림 14일) 저장, (f) 배포 뒤: `sla_due_at IS NULL`인 RETRYING 0행(MISSING_APPROVED_ESSENCE 제외), 7일 넘은 OPEN 0, 다음 월초(11/1~8) 오탐 사고 3종 0, 일일 요약 "사람의 일"이 실제 OPEN·기한 지난 건수와 일치.

**Phase 2** — (a)(b)(d)(e) 동일, (c) PENDING_DOMAIN 병원 목록·`schedule_set=false` ACTIVE 병원 목록 사전 기록, (f) 배포 뒤: Essence 자동 승인 → 일정 자동 생성 감사행 확인(신규 병원 없으면 테스트 병원으로 리허설), Beat `/live` 정상, GCP 경보 정책 2개 ENABLED, `deploy.sh`가 env diff·시간대 잠금·smoke를 실제로 거친 배포 기록.

**Phase 3** (콘텐츠 게이트, 브랜치 `claude/autonomy-p3`, [계획](2026-10-13-p3-content-gates.md)) — (a)(b)(d)(e) 동일, (c) 배포 전: 새 이미지로 `app.utils.forbidden_impact_scan`을 돌려 운영 필터 동결본 대비 **새로 막히는 글과 새로 풀리는 글**을 모두 기록하고, 영향 글 ID와 변경 전 본문(치유 전 후보)을 보관한다, `CONTENT_HARD_FINDING_IDLE_TERMINAL=false` 유지 확인, (f) 배포 뒤: 첫 복구 스윕과 07:45·08:00 발행 뒤 참고자료 0개 진료비·병원 선택 글의 공개 여부·차단 이유·재집기 횟수, `ops_sql_report --only=q8`의 거절 사유·치유 횟수, 롤백 이미지가 게이트 카탈로그 판 `2026-10-13.1`을 유지하는지.

## 6. 성공 지표 (감사 §5와 동일, 측정은 `ops_sql_report`)

| 지표 | 현재(추정) | 목표 | 측정 |
|---|---|---|---|
| 일일 요약 "사람의 일" 건수 | 항상 >0 | 평상시 0~2 | Q1 + fleet heartbeat |
| 조치 필요 알림 비율 | 25~35% | ≥70% | Q2 |
| 7일 넘게 OPEN | 9종 영구 | 0 | Q1 |
| 교체 뒤 첫 시도 | `WRITER_RESULTS_EXHAUSTED` | 실제 작가 호출 | Q6(한계: 최신 시도만 읽어 '교체 직후 첫 시도'는 근사. `topic_swap_history` 항목에 첫 시도 결과를 남기는 이벤트는 Phase 1 후속) |
| OPERATOR_REQUIRED 글/월 | — | −70% | Q4 |
| 월초 오탐 사고 | 3종/월 | 0 | Q1 (11월 1~8일) |
| 온보딩 리드타임 | 사람 대기 2곳 | 프로필 완료 + 2일 | Q5 + 감사행 |
| 배포당 사람 시간 | 1~2h + env 대조 | <20분 | 릴리스 기록 |
| 자기 장애 2차 감지 경로 | 0 | ≥1 | GCP 경보 정책 |

## 7. 범위 밖 (기록만)

Phase 3(대표 결정 필요): 월간 보고서 opt-in 자동 전달(수신자·동의 모델 + outbox + Resend), 활성화 게이트 축소 플래그 켜기, 질문 단위 비교, 진료비·병원 선택 주제의 생성 단계 각도 강제, 기본 주소 선공개. 미결 정리 8건(감사 §6)은 Phase 1 배포 뒤 별도 PR.
