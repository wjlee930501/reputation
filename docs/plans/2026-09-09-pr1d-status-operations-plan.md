# PR-1D `/hospitals/{id}` 현황 + `/operations` 예외 인박스 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 병원 현황 화면이 `GET /overview` 한 번으로 3상태 카드(남은 조건을 사람 몫/시스템 몫으로 분리) · 예외 카드(서버가 허용한 행동만 버튼) · 이번 달 요약을 보여주고, 운영 센터가 인시던트 담당 지정/변경(H-15)을 포함한 "사람 처리 필요" 항목만 다루게 한다. 재실행 버튼·측정 로그·운영 흐름 점·노출 우선순위·질문별 언급률은 현황에서 사라진다(설계 §4.3).

**Architecture:** 백엔드 `ExceptionCard`가 인시던트의 실행 가능한 행동(`OperationsAction` 목록: 재시도·복구 확인·문제 확인·담당 지정)과 라우팅 가능한 `href`를 내려주고, admin은 운영 센터의 기존 mutation 배관(`useOperationsCenter`·`primaryOperationsMutation`)을 재사용해 카드에서 바로 실행한다. 예외 초안 카드는 초안 id로 본문을 읽어 finding별 편집 → 재검수/예외 승인을 카드 안에서 처리한다(essence 화면의 라우트 3개 재사용). 인시던트 자동 배정(`HospitalHandoff.ae_owner_id`, 없으면 OWNER)과 배정 API 노출은 PR-0D Task 5(H-15)를 이 PR로 당겨온다. 옛 `dashboard` 탭·페이지는 PR-1E까지 남는다.

**코드 사실:** 2026-09-09 탐색 기준 — `hospital_overview.py:40-137,205-227`, `schemas/hospital_overview.py`, `operations_center_serializers.py:154-176,204-216,235-271`, `operations_center_incident_routes.py:80-225`, `operations_center_actions.py:51-63,96-102,185-201`, `services/incidents.py:148-166`, `essence.py:1455-1800`, `essence/page.tsx:211-231,441-548,1010-1091`, `dashboard/page.tsx` 섹션 표, `admin/app/operations/*`, `admin/lib/operations-center.ts:40-44,135-161,475-529`.

---

## 환경 준비
PR-0C 계획의 env 명령(worktree `reputation-phase1`에서 실행; `.venv`·`node_modules`는 본 체크아웃과 공유). backend 전체 suite는 Task 5에서만.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `backend/app/schemas/hospital_overview.py` | `ExceptionCard`에 `hospital_id`·`incident_id`·`operation_run_id`·`content_id`·`actions: list[OperationsAction]`·라우팅 가능 `href` |
| `backend/app/api/admin/hospital_overview.py` | 카드 행동 구성(행 `action`·`retry`·assign) + `_CONDITIONS` href → `/info`·`/content` |
| `backend/app/api/admin/operations_center_serializers.py`, `schemas/operations.py` | 인시던트 행에 `assign: OperationsAction|None`(OWNER), `IncidentDetailResponse.assignable_accounts` |
| `backend/app/services/incidents.py` | `open_or_touch_incident` 첫 open 시 자동 배정(`ae_owner_id` → OWNER 폴백) |
| `admin/app/hospitals/[id]/page.tsx` (redirect → 현황 화면) + `status/StatusCards.tsx`, `status/ExceptionCards.tsx`, `status/EscalatedDraftCard.tsx`, `status/MonthSummary.tsx` | 현황 |
| `admin/lib/status-screen.ts` (신규) | 남은 조건 분리·요약 문구·카드 행동 매핑(순수 함수) |
| `admin/app/operations/OperationDetail.tsx`, `admin/lib/operations-center.ts`, `admin/types/index.ts` | 담당 지정/변경 UI·mutation `ASSIGN_INCIDENT` |
| `admin/app/hospitals/[id]/layout.tsx` | MAIN_TABS 맨 앞에 `{ label: '현황', path: '' }`(옛 `운영 요약` 탭은 PR-1E 삭제) |

---

### Task 1: 백엔드 — 예외 카드에 실행 가능한 행동, 인시던트 배정 API·자동 배정 (H-15)

**Files:** 위 백엔드 4개 + `backend/app/api/admin/operations_center_incident_routes.py`(assignable accounts) · Tests: `tests/integration/test_hospital_overview_postgres.py`, `tests/test_operations_center_api.py`, `tests/integration/test_attention_queue.py`, `tests/test_incidents_*.py`(자동 배정)

- [ ] **Step 1: 테스트**
  - overview: OPEN 인시던트(연결 run FAILED·재시도 가능) → 카드 `actions`에 `OPEN_INCIDENT`(GET, `href=/operations?queue=incidents&hospital_id={hid}&detail=incident:{iid}`), `RETRY_RUN`(POST, `requires_idempotency_key`), `ASSIGN_INCIDENT`(POST `/api/admin/operations/hospitals/{hid}/incidents/{iid}/assign`, `requires_version`, `enabled`는 요청 actor가 OWNER일 때만) — 행동 코드는 서버 규칙(`requires_operator_action`, `authorize_run_retry`, `require_owner`)과 같음; `incident_id`·`operation_run_id`·`content_id`·`version` 포함. RETRYING(창 내)은 카드 없음(기존).
  - 인시던트 행/상세: `OperationsQueueRow.assign`(OWNER면 enabled), `IncidentDetailResponse.assignable_accounts: [{id, name, email}]`(활성 운영자, 테스트 계정 제외).
  - 자동 배정: `open_or_touch_incident` 첫 open 시 `owner_id = handoff.ae_owner_id or 활성 OWNER 1명`; 재open(episode 증가)은 기존 owner 유지; 배정 감사 detail `auto_assigned: true`.
  - `_CONDITIONS` href: `profile_complete`·`sources_required` → `/hospitals/{id}/info#info-director`/`#info-channels`, `schedule` → `/hospitals/{id}/content#content-schedule`.
- [ ] **Step 2: 실패 확인.** **Step 3: 구현** — `ExceptionCard.actions`는 `OperationsAction` 재사용(`schemas/operations.py:121-129`); `allowed_actions`는 `[a.kind for a in actions if a.enabled]`로 유지(하위 호환). assign 행동은 `serialize_incident_row(..., actor)`에서 만들고 overview는 같은 직렬화를 통과한다(`load_operator_incident_groups`에 actor 전달). 자동 배정은 `services/incidents.py::open_or_touch_incident`에서 `state`가 새로 OPEN이 될 때만.
- [ ] **Step 4:** 위 테스트 파일 + `tests/integration/test_published_image_recertification_postgres.py`(인시던트 open 경로 회귀) → PASS; ruff. **Step 5: 커밋** `feat: exception cards carry executable actions; incidents are auto-assigned and assignable (H-15)`.

---

### Task 2: admin 현황 화면

**Files:** `admin/app/hospitals/[id]/page.tsx`(redirect 제거 → 화면), `status/*.tsx`, `admin/lib/status-screen.ts` + test, `admin/lib/status-page-contract.test.ts`(신규), `admin/types/index.ts`, `layout.tsx`(탭), `scripts/check_user_facing_terms.py`(`NEW_SURFACE_PATHS` += status 파일)

- [ ] **Step 1: 테스트** — `status-screen.ts`: `splitRemaining(remaining) → {human: [...], system: [...]}`; `stateCardCopy(card)`(라벨 + "할 일 N개 · 시스템 처리 중 N개"); `monthSummaryLines(month)` → `['공개 3 / 발행 5 / 계획 12편', '보류 2편', '언급률 12% (9/1 측정)', '다음 보고서 10월 1일']`(측정 없음 → '측정 결과 없음'); `cardActionsFor(exception)` → 서버 `actions`를 `OperationsAction[]`로 그대로(enabled만 버튼, 나머지 문구). 소스 계약: page.tsx는 `useHospitalHeader().overview`만 쓰고 `fetchAPI(` 호출이 없음(예외 초안 편집 컴포넌트와 mutation hook 제외), `재실행`·`측정 실행 로그`·`운영 흐름`·`노출 개선 우선순위`·`질문별` 문자열 없음; `ExceptionCards`는 `primaryOperationsMutation`/`useOperationsCenter`의 mutation 함수를 재사용(중복 fetch 코드 없음).
- [ ] **Step 2: 구현** — `page.tsx`: 3 `StatusCard`(헤더 칩이 요약이면 여기서는 남은 조건 전체 목록 — 사람 몫은 링크, 시스템 몫은 "자동 처리 중"), `ExceptionCards`(인시던트: 제목·근거·다음 행동·버튼들(`RETRY_RUN` 사유+멱등키, `RECOVER_INCIDENT`/`ACK_INCIDENT` 사유+version, `ASSIGN_INCIDENT` 담당 select(OWNER) — 모두 운영 센터의 mutation 배관 재사용; `OPEN_INCIDENT` 링크는 운영 센터 딥링크), 예외 초안: `EscalatedDraftCard`(초안 본문 `GET /essence/philosophies`에서 id로 선택, finding 목록, 관련 필드 편집(`PhilosophyPatch` 8필드 `<details>`), "자료 기준 자동 재검수"(확인문·429 쿨다운 표시), "예외 승인"(검토자 자동, 사유 ≥20자 필수, 근거 검토 체크) — essence 화면의 세 핸들러를 `admin/lib/essence-actions.ts`로 추출해 양쪽이 공유), `MonthSummary`. 빈 예외: "지금 사람이 결정할 항목이 없습니다."
- [ ] **Step 3:** admin 검사·copy-guard·site 310. **Step 4: 커밋** `feat: hospital status screen — three state cards, executable exception cards, month summary`.

---

### Task 3: `/operations` — 담당 지정/변경, 용어 정리

**Files:** `admin/app/operations/OperationDetail.tsx`, `admin/lib/operations-center.ts`(`OperationsMutationKind` += `'ASSIGN_INCIDENT'`, body `{expected_version, reason, owner_id}`), `admin/app/operations/useOperationsCenter.ts`, `admin/types/index.ts`(`STATUS_LABELS` `ANALYZING: '초기 진단 보고서 준비 중'`, `BUILDING: '병원 공개 페이지 준비 중'`; `OperationsQueueRow.assign`, `IncidentDetailResponse.assignable_accounts`), `OperationDetail.tsx:151` 문구 `인수 대기열` → `계약 인수`, `operations-center.ts:303` 주석; `scripts/check_user_facing_terms.py` `NEW_SURFACE_PATHS` += `admin/app/operations/*.tsx`, `admin/lib/operations-center.ts`; tests `admin/lib/operations-center.test.ts`(assign mutation·라벨), `admin/lib/admin-copy.test.ts`

- [ ] 담당자 섹션에 select(`assignable_accounts`) + "담당 지정"(OWNER만 enabled; 사유 필수) → `POST .../assign`; 성공 시 상세·목록 갱신. 테스트: mutation body·403/409 처리·비OWNER 비활성.
- [ ] 커밋 `feat: operations center — assign or change the incident owner; unified status terms`.

---

### Task 4: 백엔드 문구 정렬
- [ ] `operations_center_serializers.next_onboarding_step`(`:302-313`)의 `콘텐츠 허브`·`스케줄 탭`·`온보딩 체크리스트`·`도메인 화면` → `병원 공개 페이지`·`콘텐츠 화면의 발행 요일`·`병원 정보 화면의 남은 필수 항목`·`병원 정보 화면의 자기 도메인`; 관련 테스트 갱신. 커밋 `fix: onboarding next-action copy points at the new screens`.

### Task 5: 검증·기록
- [ ] 깨끗한 DB → backend 전체 → admin/site → copy-guard → db-budget-guard; 이 문서 "실행 결과"; 등록부 H-15 해결 표기.

## 자기 점검
- §4.3 현황: 3상태 카드·남은 조건 분리(T2), 예외 카드=서버 허용 행동만(T1/T2), 예외 초안 finding→편집→재검수/예외 승인(T2), 이번 달 요약(T2), 없음 목록(T2 소스 계약). §4.3 operations: 담당 지정/변경(T1/T3), RUNNING·창 내 RETRYING 제외 유지(기존), 문구(T3/T4). §4.5 overview 확장은 하위 호환(`allowed_actions` 유지).
- 설계 밖 결정: 인시던트 자동 배정·배정 UI를 PR-0D에서 이 PR로 이동(H-15가 HITL 핵심이라 체크포인트 2에 포함).
