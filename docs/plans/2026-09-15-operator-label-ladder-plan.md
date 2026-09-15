# 운영자 라벨 정리 — 자동 폴백 사다리 완성 계획

문서 버전: 1.6 · 작성일: 2026-09-15 (Asia/Seoul) · 기준 커밋: `31dbb38`
상태: 구현 완료(미커밋·미배포). A·B1·B2·C1 모두 코드 리뷰 교차검증 지적 반영. 검증: ruff 통과, backend pytest 4006 passed / 6 failed(모두 main에서도 실패하는 로컬 postgres 역할 부재), admin 624 passed, copy-guard·db-budget-guard 통과. 배포 시 Alembic 0079 필요.

## 배경과 판정 기준

Admin 병원 목록에 `공개 후 확인 필요 N건`, `예외 N건` 같은 라벨이 병원마다 붙어 있다.
운영 철학은 "AI가 자율 운영하고 폴백까지 처리하며, 사람은 잘 돌아가는지 확인하고
원장 보고서 커뮤니케이션에 집중한다"이다. 그 기준으로 지금 사람에게 넘어오는 것을 넷으로 나눈다.

| 구분 | 내용 | 처리 |
|---|---|---|
| A | 자동 복구가 소유해야 하는데 실제로는 방치되거나 라벨만 붙는 것 | 디스패치·기한 수정 |
| B | 사람이 안 봐도 되는데 보여주는 것 | 인시던트/라벨 제거, 상태 필드로만 남김 |
| C | 자동 사다리가 끊겨 사람에게 넘어오는 것 | 마지막 폴백 계단 추가 |
| D | 사람만 할 수 있는 것 | 유일하게 라벨이 남는 집합 |

**단일 판정 규칙(변경 없음):** `requires_operator_action(state, sla_due_at, now)`
(`backend/app/api/admin/operations_center_serializers.py:343`). OPEN이거나 RETRYING인데
기한이 지난 인시던트만 사람의 일이다. 이 계획은 규칙을 바꾸지 않고 규칙에 들어가는
**입력(기한·인시던트 생성 여부·재시도 클래스·실제 디스패치)** 을 고친다.

**D 집합(라벨이 남는 것):** 계약 등록, 병원 정보·공개 주소 결정, 사진 사용 권리·사진 승인,
원장 보고서 생성 누락·전달 누락, 승인된 병원 정보 자체가 틀렸다는 HARD 지적
(`INPUT_CHANGE_REQUIRED`), 비용 가드 전체 중지(`COST_BLOCKED` 수동 해제), 자동 폴백이
모두 소진된 슬롯(`OPERATOR_REQUIRED`), 승인 base가 없는 병원의 Essence 보류 초안,
공개 이미지 재인증의 종결 코드(`REJECTED`·`MISSING`·`UNRECOVERED`), 업로드 자료 처리 실패
(`SOURCE_PROCESSING_FAILED`).

## 1차 교차검증에서 확인된 사실 (설계 수정 근거)

- 당일 슬롯 실패의 실제 생애: 23:00 야간 배치는 `[내일, 모레]`
  (`nightly_generation_batch.py:_nightly_generation_stmt`), 01·04·07시 복구 스윕은 `[오늘, 오늘]`
  (`tasks.py:overnight_content_generation_recovery`), 백로그 복구는 `scheduled_date < 오늘-7일`만
  옮긴다(`content_backlog_recovery.py:_stranded_content_stmt`). 따라서 오늘 07시 스윕까지
  실패한 슬롯은 **다음 날부터 7일간 어느 스윕도 집지 않는다.** "하루 2회 × 3일" 예산은 코드
  상 정의만 있고 실제로는 두 밤 + 당일 세 번 뒤 방치된다.
- 07:45·08:00 게이트(`tasks.py` prepublish/publish 경로)는 차단 원인을 다시
  `open_generation_incident`로 열어 RETRYING 기한을 23:00으로 옮긴다. 그래서 누수 창은
  07:00→07:45, 23:00→(방치 기간 내내)이다. 08:00 한 시점만 검사하는 테스트는 버그를 놓친다.
- Essence 보류 초안 인시던트(`ESSENCE_AUTO_REVIEW_ESCALATED`)는 `previous_philosophy_id is None`
  일 때만 열리고(`tasks.py` essence 자동 검수 종결부), 승인 base가 있으면 자동 검수가
  `UP_TO_DATE`로 끝난다(`essence_auto_review.py`). 즉 보류 초안은 사실상 **base 없는 병원**에서만
  남고 그때는 생성이 막힌다. → 사람의 일(D). 1차 설계의 B3(보류 초안 강등)은 **철회**한다.
- 채널 URL fetch 실패 자료는 `raw_text`가 비어 필수 자료 술어
  (`essence_sources.required_text_source_predicate`)에서 **즉시** 제외된다. 모두 실패하고
  다른 자료가 없으면 `required_sources=0` → 콘텐츠 상태 카드의 `sources_required`가 사람을
  부른다. 인시던트 없이도 사람 경로가 있다. 단 `raw_text`가 있는 ERROR 자료(처리 실패)는
  `SOURCE_PROCESSING_FAILED`가 담당하며 이번 범위 밖이다.

## A. 자동 복구가 약속한 재시도를 실제로 수행하게 한다

### A1. 복구 스윕이 catch-up 창의 슬롯을 다시 집는다 (핵심)
- `overnight_content_generation_recovery`의 선택 창을 `[auto_publish_catchup_start(today), today]`
  로 넓힌다. `GenerationBatchRecorder(db, task_id, window_start, window_end)`도 같은 창을
  기록한다(OperationRun 요청 payload가 실제 창과 일치해야 한다).
- **후보 선택 단계에서 재시도 불가 행을 거른다 — 워커와 같은 규칙으로.** 지금은 SQL이
  최대 101행을 읽고 라운드로빈 뒤 50개를 claim한 다음 워커가 `_generation_attempt_is_unchanged`
  (입력 컨텍스트가 같고 `retry_is_due`가 거짓)로 SKIPPED 처리한다. 창을 넓히면 그런 행이
  상한을 차지한다. 따라서 로더가 **claim 전에** 같은 함수를 적용한다.
  - SQL 술어는 바꾸지 않는다(시도 기록의 `retry_class`를 SQL에서 거르면 Essence·target·게이트
    카탈로그가 바뀌어 다시 시도해야 할 행까지 잃는다. 컨텍스트 비교는 Python에서만 가능).
  - Python: 페이지의 각 행에 `_generation_attempt_is_unchanged(item, philosophy)`를 적용해 참인
    행은 claim하지 않고 버린다. 시도 기록이 없는 행은 항상 통과한다(`retry_is_due({})`는
    거짓이지만 `unchanged`가 먼저 거짓). philosophy는 페이지의 병원별로 한 번씩 읽어 캐시한다.
  - **keyset refill:** 적격 행이 50개에 못 미치면 정렬 키(이월 여부, 예정일, 순번, id) 기준으로
    다음 101행을 읽어 창이 소진될 때까지 반복한다(안전 상한 20페이지). 고정된 작은 페이지
    상한은 매 스윕 같은 비적격 행을 다시 훑어 그 뒤의 행을 굶긴다. truncated 수는 이미 읽었지만
    상한 때문에 claim하지 못한 적격 행 + 마지막 페이지 뒤에 남은 적격 행 존재 여부로 계산한다.
  - 같은 로더를 23:00 야간 배치도 쓴다. 워커의 기존 검사는 방어선으로 남긴다. 로더가 거른
    행은 SKIPPED로 기록되지 않으므로 `GenerationBatchRecorder` 합계가 바뀐다(테스트로 고정).
- 재시도 예산과 `OPERATOR_REQUIRED` 전이 규칙은 그대로다. 이 변경으로 "3일 소진 → OPEN"이
  처음으로 실제로 일어난다. 백로그 복구(`< 오늘-7일`)와는 창이 겹치지 않는다. 07:45 게이트는
  이미 catch-up 창을 본다. claim TTL 2시간·stuck 30분 유예는 창과 무관하다.

### A2. 기한을 "그 슬롯의 실제 다음 시도 시각"으로 통일
- `generation_retry_policy.next_recovery_deadline(attempt, *, scheduled_date, now) -> datetime | None`
  추가. 스윕마다 창이 다르므로 **후보 스윕 시각을 시간순으로 열거하고, 각 스윕의 창에 예정일이
  드는 첫 시각**을 고른다.
  - 스윕 `T`(KST, 시각 `h ∈ RECOVERY_SWEEP_HOURS`)의 창: `h == 23`이면 `[date(T)+1, date(T)+2]`
    (야간 배치), 아니면 `[date(T)-7, date(T)]`(복구 스윕, catch-up 창). 예: 모레 슬롯이 오늘
    23:00 전에 실패하면 오늘 23:00; 내일 슬롯이 오늘 23:00 뒤에 실패하면 내일 01:00.
  - 하루 예산 소진(`SAMPLE_*` 일일 예산)이면 `date(T) > today(KST)`인 후보만 본다.
  - `ENVIRONMENT_RECOVERABLE`: 유한 예산(`ENVIRONMENT_ATTEMPT_BUDGET`) 소진 시 `None`(OPEN),
    `DAILY_RESET_ENVIRONMENT_CODES`만 다음 날 첫 적격 시각.
  - `OPERATOR_REQUIRED`·`INPUT_CHANGE_REQUIRED`: `None`. 후보를 14일 안에 못 찾으면 `None`.
  - catch-up 창보다 오래된 슬롯(어떤 생성 스윕도 집지 않음): 22:30 백로그 복구 실행 + 1시간을
    기한으로 둔다. 백로그가 날짜를 옮기면 지난 기한은 그냥 "도래"라 야간 배치가 정상 선택하고,
    옮기지 못하면 그때부터 사람에게 보인다(영원한 RETRYING+NULL 방지). 구현 검토에서 확정.
  - 본문 수리 코드(`_AUTOMATIC_BODY_REPAIR_CODES`): 수리 세션이 남아 있으면 저장된
    `OPERATOR_REQUIRED`보다 수리 예산이 우선한다. 세션 있음 → 오늘, 당일 수리 예산 소진 → 내일,
    전부 소진 → `None`(OPEN). 게이트 관측·워커 기록 두 경로가 같은 답을 낸다.
- `_remember_generation_attempt`가 `next_retry_at`에 이 값을 저장한다(단일 persisted decision).
  `retry_is_due`의 시각 판정은 저장값을 그대로 읽으므로 자동으로 일치한다.
- `generation_incident_control`의 `sla_due_at` 결정표:

  | 경로 | `sla_due_at` |
  |---|---|
  | 저장된 시도 기록의 `reason == code` (게이트 재개방 포함) | 저장된 `next_retry_at` 복사 |
  | 시도 기록 없이 게이트가 먼저 여는 이미지/검수/본문 수리 차단 | `_remember_generation_attempt`로 시도 수를 올리지 않는 결정 1건을 먼저 저장(`count_attempt=False`)한 뒤 복사 |
  | `MISSING_APPROVED_ESSENCE` | RETRYING + `NULL` 유지(Essence 처리가 소유, 현행) |
  | 종결 코드(`OPERATOR_REQUIRED`·`INPUT_CHANGE_REQUIRED`) | OPEN, `sla_due_at = NULL`, 저장된 `next_retry_at` 제거 |
  | lease 활성·stale claim 보고, 시도 기록 없는 예외 경로 | 현행 정책 유지, 다른 원인의 기한을 빌리지 않음 |

- 예산 때문에 스윕이 건너뛴 항목의 기한은 연장하지 않는다. 저장값이 이미 다음 적격 시각이며,
  검사했다는 이유로 늘리면 버려진 건이 영원히 RETRYING으로 남는다.

### 테스트
- 타임라인 통합: D-2 23:00 실패 → D-1 23:00 실패 → D 01:00·04:00 실패(당일 예산 소진) →
  07:00:01·07:45·08:00·23:00:01·D+1 00:30 모두 `requires_operator_action == False` →
  D+1 01:00 스윕이 **실제로 선택·claim·공급자 호출**(mock) → 3일째 소진에서 OPEN + `NULL`.
- 예산이 남은 상태의 07시 이후 실패 → 기한 내일 01시(23:00 아님). 이틀 뒤 슬롯의 다음 적격
  시각은 내일 23:00.
- 종결·예산 소진 행 60개 뒤에 있는 적격 행 1개가 상한 안에서 디스패치된다(101행 경계 포함).
- 이미지 4회 예산, KST 자정 초기화, 환경 오류 유한 예산 vs 일일 초기화 코드 구분.
- 시도 기록 없음·레거시 기록·불일치 기록에서의 `sla_due_at` 결정표 각 행. Essence `NULL` 예외.
- `next_retry_at == sla_due_at` 불변식(게이트 재개방 포함). 활성 claim·만료 claim 동작.
- 병원 목록 `open_exception_count`, 현황 카드, 오늘 큐, 주간 수율 Slack이 같은 fixture에서
  같은 답. 주간 수율은 RETRYING을 "재시도 중"으로 세는 종전 규칙 유지.

## B. 사람이 안 봐도 되는 것을 인시던트·라벨에서 제거

### B1. 공개 후 확인 표본
정책(`post_publish_review_policy.py`)이 "발행을 막지 않는 관측용 표본, 두 번째 승인 큐가 아님"
이라고 명시한다. 표본은 어떤 운영자 큐·목록 라벨에도 올리지 않는다.

- 병원 목록: 행 배지 `공개 후 확인 필요 N건`과 상단 확인 대기 묶음(unreviewed/withheld/overdue)
  제거. 상단 묶음은 **원장 보고 누락(D)** 만 남긴다.
- 운영 센터 오늘 큐(`operations_center_today_queries.py`): 표본 행을 **선택 자체에서** 뺀다.
  렌더만 숨기면 상태 CASE·severity·SLA 필터·합계·페이지네이션이 어긋난다. 관련 술어·정렬·
  합계에서 표본 분기를 함께 제거한다.
- 표본은 콘텐츠 탭 필터와 보고서 증빙(`ReportEvidence` 확인 완료/대기 수)에만 남는다.
  `ReportEvidence`의 `기한 지남 N건` 강조와 `POST_PUBLISH_REVIEW_OVERDUE_HOURS` 사용처는 모두
  제거한다(상수 자체는 API 호환을 위해 남겨도 된다).
- `/admin/operations/attention` 응답 모양은 유지, 목록 화면은 `reports`만 읽는다.
- `공개 보류(withheld)`: 재인증 스윕이 소유하는 동안은 사람의 일이 아니다. 단
  `published_image_recertification.py`의 종결 코드(`REJECTED`·`MISSING`·`UNRECOVERED`)는 D이며
  그 인시던트와 운영 센터 노출은 **그대로 유지**한다. 목록 상단에서만 뺀다.

### B2. 채널 자료 fetch 실패
`CHANNEL_SOURCE_FETCH_FAILED` 인시던트는 열지 않는다. 자료 행은 `ERROR` + `fetch_error`로
굳히고 병원 정보 탭 자료 목록에서만 보인다. 사람 경로는 콘텐츠 상태 카드(`sources_required`)가
이미 담당한다(위 확인 사실).

- 생성 지점 3곳(`tasks.py` 채널 fetch 태스크 2곳, 스윕 exhausted 처리)과
  `_reconcile_terminal_source_fetch_incidents`에서 인시던트 생성 제거.
- **종결 상태는 인시던트 id 없이 결정한다.** 현재 `terminal=incident_id is not None`과
  `status = ERROR if incident_id else FAILED`가 인시던트 성공에 묶여 있다. 영구 실패
  (`SourceRegistrationError`)와 재시도 예산 소진은 인시던트와 무관하게 `terminal=True`로
  종결하고, PENDING 상태 조건부 갱신(동시 EXCLUDED 우선)은 유지한다. fetch 재시도 예산·쿨다운
  은 바꾸지 않는다.
- 기존 열린 `CHANNEL_SOURCE_FETCH_FAILED`는 스윕이 **한도 있는 멱등 정리**로 `RECOVERED`
  (reason: `policy: source fetch failures are source state`) 처리한다. Slack·outbox를 만들지
  않는다. 혼합 버전 롤아웃 중 옛 워커가 다시 열어도 다음 스윕이 다시 닫아 수렴한다.
- 자료 행의 인시던트 링크(`admin/lib/info-sections.ts:sourceIncidentHref`)는 `ERROR` +
  `source_metadata.incident_id`만 보고 병원 인시던트 큐로 보낸다. 정리 pass는 **인시던트 상태와
  무관하게** `source_metadata.incident_id`가 가리키는 인시던트의 `incident_type`이
  `CHANNEL_SOURCE_FETCH_FAILED`인 자료를 찾아 `incident_id`를 제거하고 `incident_retired_at`을
  남긴다(이미 RECOVERED/ACKNOWLEDGED인 옛 링크, 옛 워커가 정리 뒤에 붙인 링크 모두 다음
  pass에서 수렴). 한 pass에 상한(예: 200행)을 두고 멱등하게 반복한다. `SOURCE_PROCESSING_FAILED`
  링크는 건드리지 않는다. 프론트는 `incident_id`가 없으면 링크를 만들지 않는다.
- `SOURCE_PROCESSING_FAILED`는 범위 밖(D).

### B3. (철회) Essence 보류 초안
승인 base가 있으면 보류 초안이 생기지 않고, 없으면 생성이 막히므로 사람의 일이 맞다. 목록
카운트·현황 카드·Slack 모두 현행 유지.

## C. 마지막 폴백 계단 — 본문 슬롯 주제 교체 (C1)

지금은 본문 표본 실패(`GENERATION_REJECTED`, 모델이 HARD로 단정하지 않은
`CONTENT_AI_HARD_FINDING`)가 3일 소진되면 `OPERATOR_REQUIRED`로 멈춘다(A1 이후 실제로
그렇게 된다). 이미지는 "인증 이미지 빌려 쓰기" 폴백이 있는데 본문은 없다. 슬롯의 주제를 한 번
바꿔 다시 쓰는 계단을 추가한다. **A·B와 독립된 커밋이며 A가 먼저 들어가야 한다.**

### 상태 전이 정의 (원자적)
- **삽입 지점: 로더보다 앞선 별도 pass.** 야간/복구 로더는 `FOR UPDATE SKIP LOCKED`로 고른
  뒤 claim token을 배정하고 커밋한 채 돌려주므로, 로더 뒤에서는 token이 항상 non-null이다.
  따라서 스윕 태스크 시작 시 `_swap_exhausted_topics(db, window)`를 **로더 호출 전에** 실행한다.
  이 pass는 자기 트랜잭션에서 후보를 `FOR UPDATE SKIP LOCKED`로 잠그고, claim이 없거나 만료
  (`generation_claimed_at < now - TTL`)된 행만 다룬다. 활성 claim은 건너뛴다.
- 후보 조건: 저장 시도 기록 `retry_class == OPERATOR_REQUIRED`, `reason ∈ SAMPLE_BODY_CODES`,
  모델 HARD 단정 아님(`has_model_declared_hard_finding` 거짓), `topic_swap_history` 비어 있음,
  `status ∈ 생성 대상 상태`, `first_published_at IS NULL`, **사람 편집 없음**: 새 컬럼
  `human_edited_at IS NULL`. admin 콘텐츠 PATCH(`api/admin/content.py`)가 본문·제목·meta·FAQ·
  참고자료 중 무엇이든 실제로 바꾸면 이 컬럼을 찍는다(`body_updated_at`은 공개 텍스트만 반영하므로
  쓰지 않는다). admin 콘텐츠 PATCH는 지금 감사 action을 남기지 않으므로(교차검증 확인) backfill은
  `body_updated_at > generated_at`인 행과 `brief_approved_by`가 시스템 actor가 아닌 행만
  보수적으로 채운다. 배포 전 제목·meta·FAQ만 고친 레거시 글은 식별할 수 없다는 한계를 남기며,
  그 위험은 `first_published_at IS NULL`(공개된 적 없는 글만 교체) 조건으로 제한된다.
- 후보 선택: `content_target_planner._choose_target(exclude_target_ids={현재})`. 후보가 없거나
  새 brief 주제와 기존 제목·brief의 `topic_similarity`가 임계 이상이면 교체하지 않는다.
- 쓰기: 같은 트랜잭션에서 `UPDATE content_items ... WHERE id=:id AND content_revision=:seen
  AND status=:seen_status AND (generation_claim_token IS NULL OR generation_claimed_at < :expiry)`
  로 다음을 한 번에 바꾼다(0행이면 포기, 다음 스윕에 재평가).
  - `query_target_id`, `exposure_action_id`(이전 `ExposureAction.linked_content_id` 해제),
    `content_brief`·`brief_status`(운영자 메모는 새 brief로 이월), `title`, 본문·FAQ 필드·
    참고자료·meta, 독립 검수 메타, `generated_at`, 생성 시도 기록 삭제, `content_revision + 1`,
    만료 claim은 해제.
  - 이미지: `image_url`·내용 hash·주제 hash·정책 버전·인증 시각·프롬프트·재사용/폴백 마커
    전부 비운다. 빌린 이미지의 주제 hash를 새 제목으로 만들지 않는다.
  - `topic_swap_history`(새 JSONB 컬럼, Alembic) append: `{from_target_id, from_title,
    to_target_id, reason_code, swapped_at, revision_before, revision_after,
    superseded_incident_id, superseded_episode_seq, incident_recovered: false}`.
  - `scheduled_date`·`sequence_no`·`content_type`·`schedule_id`·`carried_over_from` 유지.
- 인시던트 경계(epoch): 생성 인시던트의 dedupe key는 `_incident_identity`의 `object_id`에서
  나온다. 교체 뒤에는 `object_id = f"{item_id}#t{len(topic_swap_history)}"`를 쓴다
  (`source_id`는 종전대로 글 id라 큐 조인·성공 시 자동 종료는 그대로). 그러면 새 주제의 같은
  코드 실패는 **새 인시던트**를 열고 옛 episode를 건드리지 못하며, 같은 코드 ACK 단축 경로도
  옛 인시던트에 적용되지 않는다.
- 인시던트 정리: 커밋 뒤 history의 `superseded_incident_id`(옛 key)가 OPEN/RETRYING이면
  `RECOVERED`(reason: `topic swapped`) 처리하고 `incident_recovered=true`로 갱신한다. 크래시로
  남은 미완료 history는 다음 스윕 pass가 history 기준으로 찾아 수렴시킨다.
- 다음 시도: 시도 기록이 지워졌으므로 로더 필터를 통과하고 다음 적격 스윕이 정상 경로로
  생성한다. 두 번째 소진은 history가 있으므로 OPERATOR_REQUIRED.
- 알림: 주간 수율 메시지에 "주제 교체 n건"을 재시도 중 건수와 함께 표시(새 메시지 없음).
- 계약: `CLAUDE.md`의 "소진 3일 상한 안에서 재시도" 문장에 "본문 표본은 소진 뒤 주제 교체
  1회를 거친 뒤에만 OPERATOR_REQUIRED" 추가.

### 테스트
- 교체 1회 상한이 시도 JSON 교체·성공 생성 뒤에도 유지된다.
- 이미지 실패·모델 HARD·INPUT 코드·공개 이력 있는 글은 교체되지 않는다.
- 동시 소진·lease 만료/재claim·편집·취소·늦은 본문/이미지 응답에서 조건부 갱신이 막는다.
- 로더와의 통합: pass가 로더 앞에서 돌고, 만료된 non-null claim 행이 교체된다.
- 교체 뒤 같은 코드로 다시 실패한 새 episode는 정리 pass가 닫지 않는다.
- 선택 **전에** 사람이 편집한 글(`human_edited_at` 존재, 제목·meta·FAQ만 고친 경우 포함)은 제외된다.
- 교체 뒤 새 주제의 같은 코드 실패는 새 key로 열리고, 정리 pass는 옛 key만 닫는다.
- 같은 코드 인시던트 종결, 커밋 후 크래시 재수렴, 옛 실패 재개방 없음.
- 후보 없음·유사 주제·action 백링크·메모 이월.
- 필드 초기화 완전성과 새 이미지 인증 요구.
- 월말·이월 계정(`first_published_at`, 계약 월) 불변.
- 기존 종결 행이 배포 후 첫 스윕에서 교체되고 실제로 디스패치된다.

## D. Admin 목록·현황 최종 모양

- 목록 행: 병원명 + 3상태 필 + `예외 N건`(D 집합 인시던트 묶음 수 + base 없는 보류 초안) +
  담당 AE. `사진 승인 대기`는 D이므로 유지.
- 목록 상단: 원장 보고 누락만.
- 현황: 상태 카드 3개, 예외 카드(D). 변경 없음.
- `admin-copy.ts`의 `postPublishReview`는 콘텐츠 탭·보고서에서 계속 쓰므로 유지.

## 문서·계약 갱신

- `CLAUDE.md`: 후행 검수 문장에 "운영자 큐·목록 라벨로 올리지 않는다" 추가. 채널 자료 fetch
  실패는 인시던트가 아니라 자료 상태라는 문장 추가. 복구 스윕 창이 catch-up 7일이라는 문장
  추가. (C1 착수 시) 주제 교체 1회 규칙 추가.
- `docs/ops/slack-notification-policy.md`: (C1) 주간 수율 줄에 주제 교체 건수 항목 추가.

## 검증

- `make test-backend-local`, `make test-frontend`, `make copy-guard`.
- 통합 테스트(DB 필요) skip 여부 기록.
- 배포 후 병원 목록 `예외` 수가 운영 센터 D 집합과 일치하는지 read-only 확인.

## 작업 순서와 파일 소유

| 단계 | 범위 | 주요 파일 | 병행 |
|---|---|---|---|
| A | 스윕 창 + 기한 통일 | `generation_retry_policy.py`, `generation_incident_control.py`, `tasks.py`(스윕·`_remember`) | B1과 병행 가능 |
| B1 | 표본 라벨 제거 | `admin/app/hospitals/page.tsx`, `attention-queue.ts`, `ReportEvidence.tsx`, `operations_center_today_queries.py` | A와 병행 가능 |
| B2 | 채널 fetch 인시던트 제거 | `tasks.py`(자료 fetch 구간) | A 뒤 (같은 파일) |
| C1 | 주제 교체 | 마이그레이션, `content_target_planner.py`, `tasks.py`(스윕), 모델 | A·B 뒤, 별도 커밋 |
