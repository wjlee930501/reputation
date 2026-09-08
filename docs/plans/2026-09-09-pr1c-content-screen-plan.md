# PR-1C `/hospitals/{id}/content` 콘텐츠 화면 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 콘텐츠 화면이 월 표 + 발행 요일 + 표본 확인만으로 구성되고, 각 글의 상태가 공개 사이트와 같은 판정으로 `공개 중 / 공개 보류(사유) / 예정 / 초안 생성 중 / 차단(사유·인시던트 링크)`로 표시되며, 사람의 버튼은 §2에 있는 것만 남는다.

**Architecture:** 백엔드 `_serialize_item`이 글별 `row_state`(5값+사유)와 `operations_link`(차단 시 인시던트/실행 링크)를 내려준다 — admin은 라벨만 붙인다(PR-1A 원칙). 현재 content 페이지의 운영 복구 버튼(지금 발행·즉시 재생성·이미지만 재생성·발행일 옮기기·항목 종료·콘텐츠 가이드 편집)은 화면에서 사라지고 백엔드 라우트는 유지된다(설계 §4.5). 발행 요일 설정은 같은 화면 상단 섹션으로 들어오고, 환자 질문·노출 보완은 하단 읽기 전용이다. 옛 `schedule`·`query-targets`·`exposure-actions` 라우트는 PR-1E에서 삭제한다.

**설계 판단(명시):** §4.3은 반려를 없애지만 CLAUDE.md STEP 7과 §2 "공개 후 확인"의 부정 결과를 위해 "문제 발견 · 비공개 후 재생성"(`POST .../reject`)을 남긴다. **결함이 확인된 공개 글은 표본 여부와 무관하게 사람이 내릴 수 있다(의료광고 안전 통제)** — 상세를 연 PUBLISHED 글에는 항상 이 버튼이 있고, 되돌릴 수 없으므로 `role="alertdialog"` 확인을 거친다. 반대로 "문제 없음 · 확인 완료"는 표본(`canConfirmSample`)에만 열어 월 12~20번의 헛클릭을 만들지 않는다. 미발행 글에는 반려 버튼이 없다.

**설계 판단(명시, Astra B4):** 실패 run의 "운영 센터에서 조치" 대체 링크는 그 글에 열린(OPEN/RETRYING) 인시던트가 하나도 없을 때만 만든다 — 재시도 예산이 남았는지는 그 글의 모든 run과 subject hash가 있어야 알 수 있어 목록 질의로는 못 구하므로, 기한 안 RETRYING 인시던트를 "기계가 아직 쥔 복구"의 신호로 삼아 아무 링크도 만들지 않고(자동 복구를 사람의 할 일로 만들지 않는다), OPEN·기한 초과 RETRYING은 조치 문장이 있는 인시던트 링크가 대신한다.

**Tech Stack:** FastAPI/SQLAlchemy async, Next.js 16 + node:test.

**코드 사실:** 2026-09-09 탐색 기준(`content/page.tsx` 315-1963, `schedule/page.tsx` 65-490, `content.py` 203-1168·1391-1582, `operations.py` 838-910, `operations_center_today_queries.py` 296-333·418-422, `content_publication_block_control.py` 20-75).

---

## 실행 결과 (2026-09-09)

| Task | 커밋 | 검수 |
|---|---|---|
| 1 백엔드 `row_state`·인시던트 링크 | `6375399`(체크포인트 1에 포함) → `acb0686` | 링크를 운영 센터 쿼리 계약(`/operations?queue=incidents&hospital_id=…&detail=incident:{id}`)으로, `requires_operator_action` 적용, 최신 run만·FAILED일 때만 링크, 기한 지난 빈 초안 차단 |
| 2 월 표·상세 | `c3e8fcc` → `acb0686` → `dc537f3` | 페이지 2,102→1,350줄; 운영 복구 버튼 6종 제거; **설계 판단: 결함이 확인된 공개 글은 표본 여부와 무관하게 사람이 내릴 수 있다**(반려에 사유 필수·검증된 actor 감사) |
| 3 발행 요일 섹션·읽기 전용 신호 | `0f240b3` → `acb0686` | schedule 페이지 1:1 이식(운영 미리보기 aside 제외), 신호는 GET 전용, KST 측정일 |
| 검토 | — | Fable·Codex 각 1라운드 + 통합 수정 라운드(`acb0686`, `dc537f3`) |

## 환경 준비
PR-0C 계획의 env 명령. backend 전체 suite는 Task 6에서만.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `backend/app/services/content_row_state.py` (신규) | `content_row_state(item, visibility, *, notification_state, blocked_link) -> RowState(kind, reason, link)` — 5값 판정 단일 함수 |
| `backend/app/api/admin/operations_links.py` (신규) | `incident_href(hid, iid)`·`hospital_incidents_href(hid)` — 운영 센터 주소를 만드는 유일한 곳(admin 화면들이 공유) |
| `backend/app/api/admin/content.py` | `_serialize_item`에 `row_state`·`operations_link`; 목록 라우트가 월 항목의 인시던트/차단 run을 배치 조회 |
| `admin/lib/content-rows.ts` (신규) | `describeRowState(row_state)` 라벨/톤, 월 표 정렬, 표본 확인 가능 여부 |
| `admin/app/hospitals/[id]/content/page.tsx` | 재작성: 발행 요일 섹션 + 월 표 + 상세(읽기·편집·표본 확인) + 하단 읽기 전용 |
| `admin/app/hospitals/[id]/content/ScheduleSection.tsx` (신규) | `schedule/page.tsx`의 요일·시작일·교체 대화상자 이식(요금제 읽기 전용) |
| `admin/app/hospitals/[id]/content/ReadOnlySignals.tsx` (신규) | 이번 달 환자 질문(`in_tracking_set`) + 노출 보완 제안 읽기 전용 |
| `admin/lib/content.ts` | 운영 상태 버킷은 유지하되 `row_state` 기반으로 축약 |

---

### Task 1: 백엔드 — 글별 `row_state`와 `operations_link`

**Files:**
- Create: `backend/app/services/content_row_state.py`, `backend/tests/test_content_row_state.py`
- Modify: `backend/app/api/admin/content.py` (`_serialize_item` 1516-1582, `list_content` 446, `get_content` 484, PATCH·review·reject 응답 경로), `backend/app/schemas/content.py`
- Test: `backend/tests/integration/test_content_rows_postgres.py` (신규)

- [ ] **Step 1: 단위 테스트** — `content_row_state`:
  - PUBLISHED & visible → `("public", None, None)`; PUBLISHED & withheld → `("withheld", "대표 이미지 재인증 대기", link)` (사유 = `visibility.blocker_labels` join; **조치 링크가 있으면 공개 보류 행도 그 링크를 함께 내려보낸다** — 사유만으로는 어디로 갈지 모른다)
  - DRAFT with title/body & `compliance.publishable` & `scheduled_date >= today` → `("scheduled", None, None)`
  - DRAFT without title (야간 생성 전) → `("generating", "발행 전날 23:00 자동 생성", None)`; READY 동일. 단 `scheduled_date < today`인 빈 초안은 `("blocked", "발행일이 지났지만 아직 생성되지 않았습니다.", None)` — 이미 지나간 날짜를 "생성 중"으로 두지 않는다(본문이 있는 지연 건은 "발행일이 지났지만 아직 공개되지 않았습니다.")
  - DRAFT with title & not publishable → `("blocked", "<blockers join>", link)`; REJECTED → `("generating", "야간 재생성 대기", None)`; CANCELLED → `("closed", "종료됨", None)`(표에서 회색; 5값 외 1값 허용)
  - 링크 대상 선정: 인시던트는 `operations_center_serializers.requires_operator_action(state, sla_due_at, now)`가 참일 때만 — OPEN이거나 `sla_due_at`이 지난 RETRYING. 약속한 시간 안의 RETRYING은 자동 복구이므로 링크도 차단도 만들지 않는다. run은 그 글의 **최신** run 한 건만 보고(`ORDER BY requested_at DESC`) 그것이 FAILED일 때만 — 뒤이어 성공한 재시도가 있으면 지난 실패는 무시한다.
  - 주소는 `api/admin/operations_links.py`의 순수 함수로만 만든다: `incident_href(hid, iid)` → `/operations?queue=incidents&hospital_id={hid}&detail=incident:{iid}`, `hospital_incidents_href(hid)` → `/operations?queue=incidents&hospital_id={hid}`(run 전용 화면은 없다). 운영 센터는 `/operations` 한 화면 + 질의값이 전부다(`useOperationsCenter`의 `detail=row.id`).
- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현** — `content_row_state.py`: 위 표를 그대로 코드로; `RowState` frozen dataclass; `ROW_STATE_LABELS = {"public": "공개 중", "withheld": "공개 보류", "scheduled": "예정", "generating": "초안 생성 중", "blocked": "차단", "closed": "종료"}`. `content.py`: 목록/상세 라우트가 월 항목 id로 (a) `Incident.source_id IN ids AND state IN (OPEN, RETRYING)`를 읽고 파이썬에서 `requires_operator_action`으로 거르고, (b) `OperationRun` with `request_payload.source_id IN ids` and `operation_type IN (REGENERATE_CONTENT, REGENERATE_CONTENT_IMAGE, RECERTIFY_PUBLISHED_IMAGE)`를 `requested_at DESC`로 읽어 글마다 최신 1건만 보고 FAILED일 때만 링크 — 각 1쿼리 배치; `_serialize_item(..., blocked_link=...)`에 넘겨 `row_state={"kind","label","reason","link"}` 직렬화. `ContentItemResponse.row_state: dict` 필수. 단건 응답 경로(PATCH/review/reject/get)는 `[item.id]`로 같은 헬퍼.
- [ ] **Step 4: 통합 테스트** — 월 20건 + 인시던트 2건 + 차단 run 1건에서 목록 문장 수가 상수(`_CONTENT_LIST_STATEMENT_BUDGET`), 링크가 정확히 그 글에만 붙음. 기존 `tests/test_content_visibility.py`·`test_content_brief.py`·`test_admin_content*`·`integration/test_attention_queue.py` PASS; ruff.
- [ ] **Step 5: 커밋** `feat: every article row carries the site's judgment and its incident link`.

---

### Task 2: admin — 월 표와 상태 라벨 (운영 복구 버튼 제거)

**Files:**
- Create: `admin/lib/content-rows.ts`, `admin/lib/content-rows.test.ts`
- Modify: `admin/app/hospitals/[id]/content/page.tsx` (목록 1022-1276, 상세 푸터 1761-1963, 헤더 1300-1354, 핸들러 572-772·927-973), `admin/lib/content.ts`, `admin/types/index.ts`
- Modify tests: `admin/lib/content-async-actions.test.ts`(30·37 블록 삭제, 18 유지), `admin/lib/pending-action-key.test.ts`(21·32 content 단언 삭제), `admin/lib/post-publish-review.test.ts`(15 publish 게이트 단언 삭제), `admin/lib/operations-journey.test.ts`(`지금 발행`·`즉시 재생성` 정규식 → `확인 완료`·`문제 발견`), `admin/lib/publishing.test.ts`(12·23 유지 — `fetchCurrentAccount`는 표본 확인자 표시용으로 남김)
- Modify: `scripts/check_user_facing_terms.py` `NEW_SURFACE_PATHS` += content page·`content-rows.ts`

- [ ] **Step 1: 테스트** — `content-rows.test.ts`: `describeRowState({kind:'withheld', reason:'…'})` → `{label:'공개 보류', detail:'…', tone:'warn'}`; `blocked` with link → `{label:'차단', detail, tone:'warn', href}`; `public` → good; `scheduled`/`generating` neutral; `closed` paused. `canConfirmSample(item)` = PUBLISHED & `post_publish_review_required` & visible. 소스 계약(`content-page-contract.test.ts` 신규): page has no `handlePublish`, `handleRegenerate`, `handleRegenerateImage`, `handleReschedule`, `handleCancelSlot`, `enterBriefEditMode`, `/publish`, `/regenerate`, `/reschedule`, `/cancel`, `/brief`, `confirmAction: 'cancel'`, `'즉시 재생성'`, `'지금 발행'`, `'발행일 옮기기'`, `'콘텐츠 항목 종료'`, `'콘텐츠 가이드'`; has `describeRowState`, `canConfirmSample`, `/post-publish-review`, `/reject` exactly inside the sample block (`문제 발견 · 비공개 후 재생성`), `PATCH` edit.
- [ ] **Step 2: 구현** — 월 표 컬럼: `발행일 · 유형 · 제목 · 순번 · 상태(라벨+사유, 차단이면 링크) · 액션(표본: '확인'; 그 외 '상세')`. 요약 카드는 `row_state` 집계 5개(공개 중/보류/예정/생성 중/차단) + 이월; 필터도 같은 5값. 헤더 문구: `콘텐츠 · 발행일 08:00 자동 공개, 사람은 표본 확인만`. 상세: 헤더 `편집`(DRAFT|PUBLISHED)·닫기; 본문 읽기; 편집 모드(제목·설명·본문·참고자료, 저장 시 안내 "저장하면 자동 안전검사·재검수, 공개 글은 이미지 재인증까지 자동으로 진행됩니다"); 자동 안전검사 섹션 유지(읽기); 콘텐츠 가이드 섹션은 읽기 전용 축약(연결 질문·보완 작업만). PUBLISHED 표본: `문제 없음 · 확인 완료` / `문제 발견 · 비공개 후 재생성`(alertdialog 확인 유지); 보류 글: 사유 + 링크(재인증 대기 등)만. 다른 상태: 버튼 없음, `차단`이면 인시던트 링크 문장 "운영 센터에서 조치: …". 핸들러·상태·폴링(`regenerationRuns`, `trackedRun`) 제거.
- [ ] **Step 3:** admin `npm test/lint/typecheck`, copy-guard. **Step 4: 커밋** `feat: content screen shows the site's judgment per row and keeps only sample confirmation`.

---

### Task 3: admin — 발행 요일 섹션 이식 + 하단 읽기 전용

**Files:**
- Create: `admin/app/hospitals/[id]/content/ScheduleSection.tsx`, `content/ReadOnlySignals.tsx`
- Modify: `content/page.tsx`(조립), `admin/lib/schedule.ts`(변경 없음 확인), `admin/lib/schedule-page-r2.test.ts`(경로를 `ScheduleSection.tsx`로 재지정), `admin/lib/question-counts.test.ts`·`exposure-action-counts.test.ts`(query-targets/exposure 페이지 단언은 PR-1E까지 유지)
- Modify: `admin/app/hospitals/[id]/layout.tsx` — `발행 일정` 탭 힌트 `'월간 발행량과 발행 요일'` → `'월 발행 편수와 발행 요일'`(가드 용어)

- [ ] **Step 1: 테스트** — `schedule-page-r2.test.ts`의 두 단언을 `ScheduleSection.tsx`로; 추가: 섹션이 `useHospitalHeader().hospital.plan`을 읽고 `PLAN_CONTRACT_LABELS`로 읽기 전용 표시, `DEFAULT_PUBLISH_DAYS_BY_PLAN` 프리셋, `firstDayOfNextMonthInputValue` 기본, 교체 `role="dialog"`, `PLAN_MISMATCH` 표시. `ReadOnlySignals.tsx` 소스 계약: `GET .../query-targets`·`GET .../exposure-actions?limit=` 만 있고 `POST`/`PATCH` 없음; `in_tracking_set` 표시 문구 `측정 대상`.
- [ ] **Step 2: 구현** — `ScheduleSection`: `schedule/page.tsx` 65-490 로직 이식(상태·효과·요일 토글·시작일·저장·교체 대화상자·결과), 헤더 아래 접힘 `<details open={!existing}>`로; 저장 후 `refetchHeader()` + 월 표 재조회. `ReadOnlySignals`: 이번 달 환자 질문 목록(`name`, `target_month`, `in_tracking_set ? '측정 대상' : '측정 제외'`, `summary.latest_sov_pct`) + 노출 보완 제안(`title`, `display.status_label`, `linked_content`) — 링크·버튼 없음.
- [ ] **Step 3/4:** 검사 → 커밋 `feat: content screen — publish days and read-only patient questions and exposure suggestions`.

---

### Task 4: 문서·가드 정리
- [ ] `docs/ops/admin-korean-language-guide.md`에 5개 행 상태 용어 추가; `NEW_SURFACE_PATHS`에 `ScheduleSection.tsx`·`ReadOnlySignals.tsx`; 설계 문서 §4.3에 "표본 확인 흐름 안의 비공개 후 재생성" 판단을 한 줄 추가. 커밋 `docs: content screen terms and the sample-review takedown decision`.

### Task 5: 검증·기록
- [ ] 깨끗한 DB → backend 전체 → admin/site → copy-guard → db-budget-guard; 이 문서 "실행 결과" 기록, 등록부 갱신.

## 자기 점검
- §4.3 항목 ↔ Task: 발행 요일·요금제 읽기 전용(T3), 월 표 5상태·사이트 판정(T1/T2), 표본 확인만(T2), 상세 읽기+편집·자동 재검수 안내(T2), 금지 버튼 없음(T2 소스 계약), 하단 읽기 전용(T3).
- 백엔드 라우트 삭제 없음(§4.5). 운영 센터 대체 경로: 재생성/이미지 재생성/재인증은 `RETRY_RUN`, 인시던트는 `OPEN_INCIDENT`, 표본은 `REVIEW_CONTENT`/`OPEN_CONTENT` 딥링크(`?content=`) — 딥링크 처리(433-448)는 유지.
