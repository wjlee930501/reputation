# Phase 1 — 사고 모델을 "사람의 일"에 맞춘다 (1-1·1-2·1-3·1-4·1-5·1-8·1-10)

기준 소스: main `8c8f6e60`. 감사 문서: `docs/plans/2026-10-11-autonomy-audit-and-improvement-plan.md` §2 F3·F4·F10, §4 Phase 1.
이 절은 계획이다. 배포·운영 데이터 변경의 증거가 아니다.

---

## 0. 설계

### 0-1. 지금 구조 (코드로 확인한 사실)

| 관심사 | 현재 위치 | 형태 |
|---|---|---|
| 대상(운영/개발) | `backend/app/services/incident_types.py:56-70` `_DEVELOPER_INCIDENT_TYPES` | frozenset |
| 조용함(Slack·할 일 제외) | `incident_types.py:76` `QUIET_INCIDENT_TYPES` | frozenset, `notification_store.py:26,348`가 SQL에 씀 |
| 처음 상태·기한 | `incidents.py:79-81` 항상 `state=OPEN`, `sla_due_at` 미지정(NULL). 충돌 시 `:103` 항상 OPEN | 하드코딩 |
| RETRYING+기한으로 바로 두는 곳 | 콘텐츠 생성(`generation_incident_control.py:1189-1229`, 사후 `mark_retrying` 후 `incident.sla_due_at=`), Essence(`tasks.py:3921-3967` 별도 트랜잭션에서 OPEN→RETRYING) | 호출자별 사후 조작 |
| 닫는 근거(resolver) | `workers/incident_backlog.py:333-344` `RESOLVERS` 10종, **동기 Session**, OPEN만 대상(`:357`) | dict[str, Resolver] |
| 사람의 일 술어 | `operator_action.py:9-35` `requires_operator_action`·`is_operator_todo` | `sla_due_at is None`인 RETRYING은 영원히 사람의 일 아님(`:19`) |
| 열림 알림 발송 | `notification_store.py:338-366` `claimable_notifications_stmt`는 조용한 종류만 거른다. 사고 상태를 다시 보지 않음 | |
| 짝 규칙 | `dependency_incident_helpers.open_notice_exists`: OPEN 행이 outbox에 있으면 RECOVERED를 반드시 보낸다("a delivered OPEN must always be closed") | 계약 |

### 0-2. 데이터 모델 — 레지스트리 (`incident_types.py`, 스키마 변경 없음)

```python
@dataclass(frozen=True, slots=True)
class IncidentTypePolicy:
    audience: IncidentAudience = IncidentAudience.OPERATOR   # 감사의 audience
    quiet: bool = False                                     # 감사의 quiet
    sweep_owned: bool = False                               # 감사의 initial_state(RETRYING 여부)
    retry_window: RetryWindow | None = None                 # 감사의 deadline_fn
INCIDENT_TYPE_POLICIES: dict[str, IncidentTypePolicy]
```

- `initial_state`는 따로 저장하지 않고 **유도한다**: `sweep_owned`이고 기한(요청의 `retry_until` 또는 `retry_window(request, episode_start)`)이 지금보다 뒤면 RETRYING+그 기한, 아니면 OPEN. 기한을 못 정하면(파싱 실패·기한 경과) 항상 OPEN — 누락 시 안전한 쪽.
- `_DEVELOPER_INCIDENT_TYPES`·`QUIET_INCIDENT_TYPES`는 레지스트리에서 **유도한 frozenset**으로 남긴다(기존 import·SQL·테스트 호환). 새로 `SWEEP_OWNED_INCIDENT_TYPES`를 노출한다.
- **resolver는 레지스트리에 넣지 않는다.** 감사는 `{..., resolver}`를 한 곳에 두자고 했지만, resolver는 동기 `Session`과 `ContentItem`·`LeadDiagnosis`·`HospitalContentPhilosophy` 등 모델을 import한다. `incident_types.py`는 `notification_store`·`operator_action`·`incidents`가 import하는 바닥 모듈이라 순환 위험이 크다. resolver는 `incident_backlog.RESOLVERS`에 **같은 키**로 두고, 테스트가 "sweep_owned 종류는 모두 resolver 또는 명시적 복구 경로가 있다"를 고정한다.
- `IncidentOpenRequest`에 `retry_until: datetime | None = None`(마지막 필드, 기본값 있음)을 더한다. 호출자가 정확한 다음 시도 시각을 아는 종류(Essence)가 쓴다.

### 0-3. `open_or_touch_incident` 동작 표 (등록된 sweep_owned 종류만; 그 밖은 오늘과 같다)

| 기존 행 | 기한 남음 | 결과 state | 결과 sla_due_at |
|---|---|---|---|
| 없음 / RECOVERED / ACKNOWLEDGED | 예(에피소드 시작=지금) | RETRYING | 새 기한 |
| RETRYING | 예(에피소드 시작=기존 `first_seen_at`) | RETRYING | 다시 계산(같은 값; Essence는 호출자 값으로 갱신) |
| RETRYING | 아니오 | **OPEN** (사람에게 넘어감) | 그대로 |
| OPEN | 무관 | OPEN (사람이 가진 일을 내리지 않음) | 그대로 |

기한은 **에피소드 시작**(`first_seen_at`)에서 계산하므로, 매 터치마다 기한이 밀려 영원히 RETRYING에 머무는 일이 없다.

### 0-4. 알림 — 호출자를 바꾸지 않고 발송기가 판단한다 (1-4)

등록 종류의 호출자는 지금처럼 "처음 열림/재열림"에 INCIDENT_OPEN을 outbox에 넣는다. 발송기(`claim_notification_batch`)가:

1. 연결 사고가 **sweep_owned 종류**이고 RETRYING이며 `sla_due_at > now`이면 그 INCIDENT_OPEN을 **집지 않는다**(PENDING 유지). 기한이 지나면 자동으로 나간다 = 사람에게 넘어가는 순간의 알림.
2. 연결 사고가 RECOVERED/ACKNOWLEDGED가 된 **아직 안 나간** INCIDENT_OPEN은 `FAILED`+`INCIDENT_RESOLVED_BEFORE_SEND`로 종결한다(선례: `notification_channel_health.py:208-235`의 `STALE_NOT_RESENT`).
3. 짝 계약 보존: 그 사고의 INCIDENT_OPEN이 모두 2번으로 종결됐고 나갔을 수 있는 OPEN(PENDING·SENDING·RETRYING·HOLD·SENT)이 하나도 없으면, 그 사고의 아직 안 나간 INCIDENT_RECOVERED도 같은 코드로 종결한다("나가지 않은 OPEN에는 RECOVERED도 없다").

콘텐츠 생성(`CONTENT_GENERATION_FAILED`)은 sweep_owned에 등록하지 않는다 — 07:45 이후 예정일 미작성 글은 RETRYING이어도 아침에 알리는 계약(`generation_incident_control.py:970-1005`)이 있어서다.

### 0-5. 호출자 이전(migration)

| 종류 | 여는 곳 | 바꾸는 것 |
|---|---|---|
| `MONTHLY_SLOT_GENERATION_FAILED` | `workers/monthly_slot_incident_control.py:39-106` | 없음(레지스트리만) |
| `MONTHLY_SOV_MEASUREMENT_FAILED` | `workers/weekly_sov_incident_control.py:187-280` | 없음 |
| `SITE_BUILD_DISPATCH_FAILED` | `tasks.py:4958-4979` (`open_ops_incident`, notify=False) | 없음 |
| `CONTENT_DISPATCH_FAILED` | `api/admin/content.py:517-535` (notify=False) | 없음 |
| `ESSENCE_AUTO_REVIEW_ESCALATED` | `tasks.py:4081-4112` | `retry_until=` 전달, `_defer_essence_escalation_to_automatic_retry`(`tasks.py:3921-3967`) 삭제 |

### 0-6. 작업 순서·배포

1-1a(레지스트리) → 1-4(발송기) → 1-1b(open_or_touch) → 1-1c(Essence) → 1-3(기본 기한) → 1-2(resolver) → 1-5(정착) → 1-8(재분류) → 1-10. 스키마 변경 없음. **한 번의 배포**로 묶는다(1-1b가 1-4 없이 나가도 오늘보다 나빠지지 않는다 — 열림 알림이 그냥 즉시 나갈 뿐).

### 0-7. 공통 실행 환경

- 단위 테스트: `cd backend && .venv/bin/python -m pytest tests/<file>::<test> -v`
- DB 테스트(`tests/integration/*_postgres.py`): `.github/workflows/ci.yml` backend 잡 env(`INTEGRATION_DATABASE_URL`, `DATABASE_URL`, `SYNC_DATABASE_URL`, `REDIS_URL` 등 README 59-61행 목록)를 모두 export하고 `alembic upgrade head`된 `*_test` DB가 떠 있어야 한다. 비어 있으면 변수 이름과 함께 FAIL이 정상이다(skip 아님).

### 0-8. 감사와 다른 점 (요약; 각 Task의 조사 결과에 근거)

1. **1-3은 술어가 아니라 쓰기 쪽에서 강제한다.** `MISSING_APPROVED_ESSENCE`는 의도적으로 `sla_due_at=None`(`generation_incident_control.py:1189-1202`, 사람의 일은 별도 ESCALATED 사고가 소유)이라 술어에서 24h를 강제하면 같은 원인이 두 번 세진다. 술어(`operator_action.py:19`)와 일일 요약 SQL(`fleet_heartbeat.py:306,345`)은 그대로 두고, `mark_retrying` 기본 기한 + 기존 행 정리 스윕으로 바꾼다.
2. **1-4의 보류는 등록된 sweep_owned 종류로 한정해야 한다.** 일반화하면 콘텐츠 생성의 아침 알림 계약이 깨진다.
3. **1-4는 짝 계약을 깨지 않게 RECOVERED도 함께 종결해야 한다**(감사 표에 없음).
4. **1-5의 "RETRYING+연결 실행 SUCCEEDED"는 Admin 버튼(`operations_center_serializers.py:252-262`)보다 좁혀야 한다**: 실행 완료가 마지막 관측 뒤일 것, `CONTENT_GENERATION` 출처 제외(생성 사고는 연결 실행이 SUCCEEDED여도 글이 막혀 있을 수 있고 자체 복구 코드가 있다).
5. **1-8의 `SOURCE_PROCESSING_FAILED` 강등은 하지 않는다.** `tasks.py:3822-3824` 주석(I-3): ERROR는 자동 재시도 없는 종착이고 사람이 자료를 고치거나 빼야 근거가 돌아온다. 이 사고가 유일한 신호다.
6. **1-10은 2026-10-08 대표 지시(사후검수 지적 Slack 끔, `incident_types.py:73-76`)를 일부 되돌린다.** HARD 지적이 남은 공개 글로 범위를 좁혔지만 대표 확인이 필요하다.
7. 감사 F4가 든 `LEAD_DELIVERY_ABANDONED`는 1-2 목록에 빠져 있어 함께 넣었다.
8. 1-6 ①(월초 `MONTHLY_SOV_MEASUREMENT_FAILED` RETRYING+복구 마감)은 이 계획의 1-1 레지스트리 항목과 **같은 변경**이다. 1-6 담당 계획과 중복 구현하지 않도록 조정 필요.

---

### Task 1-1a: 사고 종류 소유 정책 레지스트리

**Files:**
- Modify: `backend/app/services/incident_types.py:1-12` (import), `:53-92` (집합·조회 함수), `:114-130` (`IncidentOpenRequest`), 파일 끝(레지스트리)
- Test: `backend/tests/test_incident_type_policy.py` (신규)

**조사 결과:**
- `incident_audience`(`:85-92`)·`incident_is_quiet`(`:79-82`)는 `strip().upper()` 키로 frozenset을 본다. 사용처: `operator_action.py:6,33-34`, `notification_store.py:25-29,87,131,348`, `fleet_heartbeat.py:21,335`, `operations_center_incident_queries.py`. 집합 이름을 유지하면 사용처를 고칠 필요가 없다.
- `IncidentOpenRequest`(`:114-130`)는 모든 필드가 키워드로 생성된다(`grep -rn "IncidentOpenRequest("` 결과 위치 인자 사용 없음). 마지막에 기본값 필드를 더해도 안전하다.
- 월간 측정 복구 마감 정본: `tasks.py:8844-8848` `reporting_period(y,m).ends_at + 7일 - 1초`(= 다음 달 7일 23:59:59 KST).
- 월간 슬롯: 다음 달분은 25~31일 6시간마다(`celery_app.py:441-446`), 이번 달분은 매일 21:30(`:448-453`)에 다시 돈다. `period_key`는 `YYYY-MM`(`tasks.py:9888`), 사고 `source_id`는 `"{hospital_id}:{period_key}"`(`monthly_slot_incident_control.py:71`).
- **감사 가정과 다른 점**: 슬롯 실패는 대부분 결정적이다(발행 요일 부족 → `generate_monthly_slots` ValueError, `tasks.py:9893-9898`). 재시도로 풀리지 않으므로 기한을 짧게(24시간, 다음 달분은 월 시작 전으로 상한) 둔다. 그래도 다음 달분은 월 시작까지 최대 6일 여유가 남는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_incident_type_policy.py`

```python
"""사고 종류 소유 정책 레지스트리(Phase 1-1) — 대상·조용함·스윕 소유·기한 창."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.operations import IncidentSeverity
from app.services.incident_types import (
    DEFAULT_RETRYING_DEADLINE,
    QUIET_INCIDENT_TYPES,
    SWEEP_OWNED_INCIDENT_TYPES,
    IncidentAudience,
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_audience,
    incident_is_quiet,
    incident_policy,
    retry_deadline,
)

KST = ZoneInfo("Asia/Seoul")
HOSPITAL = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _request(incident_type: str, source_id: str | None, retry_until: datetime | None = None):
    return IncidentOpenRequest(
        pipeline="policy_test",
        object_type="hospital_month",
        object_id=str(source_id),
        fingerprint=IncidentFingerprint.VALIDATION_FAILED,
        incident_type=incident_type,
        severity=IncidentSeverity.HIGH,
        customer_impact="테스트",
        source_type="POLICY_TEST",
        next_action="테스트",
        admin_path="/operations",
        source_id=source_id,
        retry_until=retry_until,
    )


@pytest.mark.parametrize(
    "incident_type",
    [
        "BACKGROUND_TASK_FAILED",
        "OPERATION_TERMINAL_FAILED",
        "BROKER_UNAVAILABLE",
        "UNSAFE_STORED_DISPATCH",
        "NOTIFICATION_DELIVERY_FAILED",
        "NOTIFICATION_DELIVERY_UNKNOWN",
        "CACHE_REVALIDATION_FAILED",
        "MONTHLY_DOCTOR_PDF_BLOCKED",
        "PUBLIC_SITE_UNAVAILABLE",
    ],
)
def test_existing_developer_types_keep_their_audience(incident_type):
    assert incident_audience(incident_type) is IncidentAudience.DEVELOPER


def test_existing_quiet_type_is_preserved():
    assert "POST_PUBLISH_REVIEW_FLAGGED" in QUIET_INCIDENT_TYPES
    assert incident_is_quiet("post_publish_review_flagged") is True


def test_unregistered_types_are_people_owned_and_open():
    policy = incident_policy("BRAND_NEW_TYPE")
    assert policy.audience is IncidentAudience.OPERATOR
    assert policy.quiet is False and policy.sweep_owned is False
    now = datetime(2026, 10, 26, 3, tzinfo=UTC)
    assert retry_deadline(_request("BRAND_NEW_TYPE", "x"), episode_start=now, observed_at=now) is None


def test_sweep_owned_types_are_registered():
    assert {
        "MONTHLY_SLOT_GENERATION_FAILED",
        "MONTHLY_SOV_MEASUREMENT_FAILED",
        "SITE_BUILD_DISPATCH_FAILED",
        "CONTENT_DISPATCH_FAILED",
        "ESSENCE_AUTO_REVIEW_ESCALATED",
    } <= SWEEP_OWNED_INCIDENT_TYPES
    # 콘텐츠 생성은 자체 사다리와 아침 알림 계약이 있어 등록하지 않는다.
    assert "CONTENT_GENERATION_FAILED" not in SWEEP_OWNED_INCIDENT_TYPES


def test_next_month_slot_failure_gets_one_day_capped_at_month_start():
    start = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)  # 31일 18:00 KST
    deadline = retry_deadline(
        _request("MONTHLY_SLOT_GENERATION_FAILED", f"{HOSPITAL}:2026-11"),
        episode_start=start,
        observed_at=start,
    )
    assert deadline == datetime(2026, 11, 1, tzinfo=KST)


def test_current_month_slot_failure_waits_one_reconciliation():
    start = datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    deadline = retry_deadline(
        _request("MONTHLY_SLOT_GENERATION_FAILED", f"{HOSPITAL}:2026-10"),
        episode_start=start,
        observed_at=start,
    )
    assert deadline == start + DEFAULT_RETRYING_DEADLINE


def test_monthly_sov_deadline_is_the_final_judgment_time():
    start = datetime(2026, 10, 25, tzinfo=UTC)
    request = _request("MONTHLY_SOV_MEASUREMENT_FAILED", f"{HOSPITAL}:2026-10")
    deadline = retry_deadline(request, episode_start=start, observed_at=start)
    # D18(구현 중 확정): 복구 마감(7일 23:59:59)이 아니라 최종화 뒤 마지막 판정 시각 8일 09:00 KST.
    assert deadline == datetime(2026, 11, 8, 9, 0, tzinfo=KST)
    late = deadline + timedelta(seconds=1)
    assert retry_deadline(request, episode_start=start, observed_at=late) is None


def test_caller_supplied_deadline_wins_and_absence_means_people():
    now = datetime(2026, 10, 26, 3, tzinfo=UTC)
    due = now + timedelta(hours=24)
    essence = "ESSENCE_AUTO_REVIEW_ESCALATED"
    assert retry_deadline(_request(essence, "h:s", due), episode_start=now, observed_at=now) == due
    assert retry_deadline(_request(essence, "h:s"), episode_start=now, observed_at=now) is None


def test_unparseable_period_is_people_owned():
    now = datetime(2026, 10, 26, 3, tzinfo=UTC)
    request = _request("MONTHLY_SOV_MEASUREMENT_FAILED", "garbage")
    assert retry_deadline(request, episode_start=now, observed_at=now) is None
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_incident_type_policy.py -v` Expected: FAIL — `ImportError: cannot import name 'DEFAULT_RETRYING_DEADLINE' from 'app.services.incident_types'` (수집 오류).

- [ ] **Step 3: 최소 구현** — `backend/app/services/incident_types.py`

(a) import 블록(`:3-11`)을 다음으로 바꾼다.

```python
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TypeAlias
from zoneinfo import ZoneInfo

from app.models.operations import Incident, IncidentSeverity, IncidentState
```

(b) `:53-76`(`# Only pure-infrastructure ...`부터 `QUIET_INCIDENT_TYPES` 정의 끝까지)를 지운다. `:79-92`의 두 함수를 다음으로 바꾼다(정의는 파일 끝 레지스트리를 호출 시점에 읽는다).

```python
def incident_is_quiet(incident_type: str | None) -> bool:
    """이 종류의 사고는 Slack 알림을 만들지 않는다(행과 Admin 화면에는 남는다)."""

    return incident_policy(incident_type).quiet


def incident_audience(incident_type: str | None) -> IncidentAudience:
    """Return the audience registered for one incident type (default: operator)."""

    return incident_policy(incident_type).audience
```

(c) `IncidentOpenRequest`(`:114-130`) 마지막 필드 뒤에 한 줄 추가:

```python
    safe_error_message: str | None = None
    # 스윕 소유 종류에서 호출자가 아는 다음 자동 시도 시각(Essence). None이면 정책의 창을 쓴다.
    retry_until: datetime | None = None
```

(d) 파일 끝(`IncidentLabels` 뒤)에 추가:

```python
# ── 사고 종류별 소유 정책 (2026-10 Phase 1) ──────────────────────────────────────
# 대상·조용함·스윕 소유·기한을 한 표에 둔다. 닫는 근거(resolver)는 동기 세션과 도메인 모델이
# 필요해 `workers/incident_backlog.RESOLVERS`에 같은 키로 둔다(이 모듈은 바닥 모듈이라 순환 금지).

DEFAULT_RETRYING_DEADLINE = timedelta(hours=24)
_KST = ZoneInfo("Asia/Seoul")

RetryWindow: TypeAlias = Callable[[IncidentOpenRequest, datetime], datetime | None]


@dataclass(frozen=True, slots=True)
class IncidentTypePolicy:
    """한 사고 종류를 누가 소유하는가.

    - audience: 고칠 수 있는 사람. 등록하지 않으면 운영 담당 — 새 종류를 조용히 숨기지 않는다.
    - quiet: Slack을 만들지 않고 운영자의 할 일·일일 요약 건수에도 넣지 않는다.
    - sweep_owned: 스윕이 재시도를 소유한다. 기한이 남아 있으면 RETRYING+기한으로 열리고,
      기한이 지나야 사람의 일이 된다. 기한을 정할 수 없으면 OPEN(안전한 쪽).
    - retry_window: 에피소드 시작 시각에서 기한을 계산한다. None이면 호출자가 retry_until을 준다.
    """

    audience: IncidentAudience = IncidentAudience.OPERATOR
    quiet: bool = False
    sweep_owned: bool = False
    retry_window: RetryWindow | None = None


def _period_month(source_id: str | None) -> tuple[int, int] | None:
    """`"{hospital_id}:YYYY-MM"` 또는 `"YYYY-MM"`에서 연월을 읽는다. 아니면 None."""

    raw = str(source_id or "")
    _scope, separator, tail = raw.rpartition(":")
    candidate = tail if separator else raw
    try:
        year_text, month_text = candidate.split("-", 1)
        year, month = int(year_text), int(month_text)
    except ValueError:
        return None
    return (year, month) if 2000 <= year <= 2200 and 1 <= month <= 12 else None


def _one_day_window(_request: IncidentOpenRequest, episode_start: datetime) -> datetime:
    return episode_start + DEFAULT_RETRYING_DEADLINE


def _monthly_slot_window(
    request: IncidentOpenRequest, episode_start: datetime
) -> datetime | None:
    """다음 달분은 25~31일 6시간 스윕, 이번 달분은 매일 21:30 재조정이 다시 돈다.

    실패 원인이 대개 결정적(발행 요일 부족)이라 하루 안에 못 풀면 사람에게 넘긴다. 다음 달분은
    그 달이 시작되기 전에 넘겨 일정이 비기 전에 사람이 볼 수 있게 한다.
    """

    period = _period_month(request.source_id)
    if period is None:
        return None
    deadline = episode_start + DEFAULT_RETRYING_DEADLINE
    period_start = datetime(period[0], period[1], 1, tzinfo=_KST)
    return min(deadline, period_start) if period_start > episode_start else deadline


def _monthly_sov_window(
    request: IncidentOpenRequest, _episode_start: datetime
) -> datetime | None:
    """월간 측정은 다음 달 8일 09:00 KST(`monthly_period.monthly_final_judgment_at`)까지 자동 복구가 맡는다.

    D18: 계획 당시 예시는 복구 마감(다음 달 7일 23:59:59)이었으나, 00:00~00:15 사이 보류 알림이 나간 뒤
    8일 00:15 최종화가 닫는 오탐을 막으려고 최종 판정 시각으로 옮겼다.
    """

    period = _period_month(request.source_id)
    if period is None:
        return None
    year, month = period
    next_start = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=_KST)
    return next_start + timedelta(days=7) - timedelta(seconds=1)


_DEVELOPER = IncidentTypePolicy(audience=IncidentAudience.DEVELOPER)
_DEFAULT_POLICY = IncidentTypePolicy()

INCIDENT_TYPE_POLICIES: dict[str, IncidentTypePolicy] = {
    # 순수 인프라 — 운영 담당이 Admin에서 고칠 수 없다.
    "BACKGROUND_TASK_FAILED": _DEVELOPER,
    "OPERATION_TERMINAL_FAILED": _DEVELOPER,
    "BROKER_UNAVAILABLE": _DEVELOPER,
    "UNSAFE_STORED_DISPATCH": _DEVELOPER,
    "NOTIFICATION_DELIVERY_FAILED": _DEVELOPER,
    "NOTIFICATION_DELIVERY_UNKNOWN": _DEVELOPER,
    "CACHE_REVALIDATION_FAILED": _DEVELOPER,
    "MONTHLY_DOCTOR_PDF_BLOCKED": _DEVELOPER,
    # 같은 점검에서 여러 병원 주소가 공유 공개 서비스의 5xx를 받은 상태
    # (`domain_health_control.record_public_site_check`).
    "PUBLIC_SITE_UNAVAILABLE": _DEVELOPER,
    # 사후검수 지적은 계약상 운영자 큐에 올리지 않는 표본 확인이다(2026-10-08 대표 지시). 콘텐츠 탭에서 본다.
    "POST_PUBLISH_REVIEW_FLAGGED": IncidentTypePolicy(quiet=True),
    # 스윕이 재시도를 소유한다 — 기한 안에서는 사람의 일이 아니다.
    "MONTHLY_SLOT_GENERATION_FAILED": IncidentTypePolicy(
        sweep_owned=True, retry_window=_monthly_slot_window
    ),
    "MONTHLY_SOV_MEASUREMENT_FAILED": IncidentTypePolicy(
        sweep_owned=True, retry_window=_monthly_sov_window
    ),
    # 사이트 준비는 1분 자율 복구(`autonomous_recovery.reconcile`)가, 가까운 글 생성은 야간·복구
    # 스윕이 다시 집는다. 큐에 못 넣은 사실만으로는 사람의 일이 아니다.
    "SITE_BUILD_DISPATCH_FAILED": IncidentTypePolicy(
        sweep_owned=True, retry_window=_one_day_window
    ),
    "CONTENT_DISPATCH_FAILED": IncidentTypePolicy(sweep_owned=True, retry_window=_one_day_window),
    # 자동 재검수 예산이 남은 보류. 다음 시도 시각은 호출자가 안다(`retry_until`).
    "ESSENCE_AUTO_REVIEW_ESCALATED": IncidentTypePolicy(sweep_owned=True),
}


def incident_policy(incident_type: str | None) -> IncidentTypePolicy:
    if not incident_type:
        return _DEFAULT_POLICY
    return INCIDENT_TYPE_POLICIES.get(incident_type.strip().upper(), _DEFAULT_POLICY)


_DEVELOPER_INCIDENT_TYPES: frozenset[str] = frozenset(
    key for key, policy in INCIDENT_TYPE_POLICIES.items()
    if policy.audience is IncidentAudience.DEVELOPER
)
QUIET_INCIDENT_TYPES: frozenset[str] = frozenset(
    key for key, policy in INCIDENT_TYPE_POLICIES.items() if policy.quiet
)
SWEEP_OWNED_INCIDENT_TYPES: frozenset[str] = frozenset(
    key for key, policy in INCIDENT_TYPE_POLICIES.items() if policy.sweep_owned
)


def retry_deadline(
    request: IncidentOpenRequest, *, episode_start: datetime, observed_at: datetime
) -> datetime | None:
    """스윕 소유 사고의 남은 자동 재시도 기한. 소유가 아니거나, 정할 수 없거나, 지났으면 None."""

    policy = incident_policy(request.incident_type)
    if not policy.sweep_owned:
        return None
    deadline = request.retry_until
    if deadline is None and policy.retry_window is not None:
        deadline = policy.retry_window(request, episode_start)
    if deadline is None or deadline <= observed_at:
        return None
    return deadline
```

`IncidentState` import는 1-1b에서 쓰지 않으면 지운다(`ruff`가 알려 준다 — 기존 파일은 `IncidentState`를 import만 하고 있었다면 그대로 둔다).

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_incident_type_policy.py tests/test_quiet_incident_notifications.py tests/test_notification_outbox.py -v` Expected: 모두 PASS(기존 대상·조용함 테스트 회귀 없음).

- [ ] **Step 5: 커밋** `git add backend/app/services/incident_types.py backend/tests/test_incident_type_policy.py && git commit -m "feat(incidents): 사고 종류 소유 정책 레지스트리(대상·조용함·스윕 소유·기한 창)"`

**계약 영향:** CLAUDE.md "Slack은 알림 정책을 따른다… `incident_types.QUIET_INCIDENT_TYPES`에 등록한 사고는…" 문장을 "`incident_types.INCIDENT_TYPE_POLICIES`의 quiet로 등록한 사고는…"으로 바꾼다.
**위험·롤백:** 집합을 유도형으로 바꾸며 철자 하나가 빠지면 대상이 바뀐다 — Step 4의 기존 파라미터 테스트가 막는다. 롤백은 커밋 되돌리기.

---

### Task 1-4: 발송기가 기한 안의 스윕 소유 사고·이미 끝난 사고의 알림을 보내지 않는다

**Files:**
- Modify: `backend/app/services/notification_store.py:9-30` (import), `:338-366` (`claimable_notifications_stmt`), `:369-415` (`claim_notification_batch`)
- Test: `backend/tests/integration/test_incident_notice_settlement_postgres.py` (신규)

**조사 결과:**
- 발송기는 조용한 종류만 거르고 사고 상태를 다시 보지 않는다(`notification_store.py:338-366`). 감사 F3 확인.
- 열림 알림 dedupe 키는 `INCIDENT_OPEN:{incident_id}:e{episode_seq}`(`notification_messages.py:139-141`) — 에피소드당 한 행. 보류해도 중복이 생기지 않는다.
- 짝 계약(`dependency_incident_helpers.py` `open_notice_exists`): OPEN 행이 outbox에 **있으면** 복구 시 RECOVERED를 넣는다. 그래서 OPEN만 종결하면 "나가지 않은 OPEN의 RECOVERED"가 나간다 → RECOVERED도 함께 종결해야 한다(**감사에 없음**).
- 종결 선례: `notification_channel_health.py:208-235`가 해결된 사고의 보류 행을 `FAILED`+`STALE_NOT_RESENT`로 닫는다. `ck_notification_outbox_retry_schedule`(`models/operations.py:276-281`)상 FAILED는 `next_attempt_at IS NULL`이어야 한다.
- 콘텐츠 생성은 RETRYING이어도 07:45 이후 예정일 미작성 글을 아침에 알린다(`generation_incident_control.py:970-1005`). 보류를 전 종류로 넓히면 이 계약이 깨진다 → **sweep_owned 종류로 한정**(감사 표의 "RETRYING(기한 안)"을 좁힘).
- outbox 나이 감시는 없다(`grep NotificationOutbox.created_at <` → 채널 보류 정리 한 곳). 보류 행이 며칠 PENDING이어도 다른 경보를 만들지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/integration/test_incident_notice_settlement_postgres.py`

```python
"""발송기는 기한 안의 스윕 소유 사고와 이미 끝난 사고의 알림을 보내지 않는다(Phase 1-4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.operations import Incident, NotificationOutbox
from app.services.notification_store import (
    RESOLVED_BEFORE_SEND_CODE,
    claim_notification_batch,
)

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def _incident(incident_type: str, state: str, *, sla: datetime | None = None) -> Incident:
    closed = state in ("RECOVERED", "ACKNOWLEDGED")
    return Incident(
        dedupe_key=f"notice-test:{uuid.uuid4()}",
        incident_type=incident_type,
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type="TEST",
        next_action="테스트",
        admin_path="/operations",
        sla_due_at=sla,
        first_seen_at=NOW - timedelta(hours=1),
        last_seen_at=NOW - timedelta(hours=1),
        recovered_at=NOW - timedelta(minutes=30) if closed else None,
        acknowledged_at=NOW - timedelta(minutes=10) if state == "ACKNOWLEDGED" else None,
    )


def _notice(incident: Incident, kind: str = "INCIDENT_OPEN", state: str = "PENDING"):
    pending = state in ("PENDING", "RETRYING")
    return NotificationOutbox(
        dedupe_key=f"{kind}:{incident.id}:{uuid.uuid4()}",
        notification_type=kind,
        channel="SLACK",
        state=state,
        incident_id=incident.id,
        payload={},
        fallback_text="테스트",
        next_attempt_at=NOW - timedelta(minutes=5) if pending else None,
        sent_at=NOW - timedelta(minutes=20) if state == "SENT" else None,
    )


async def _seed(db, incident: Incident, *notices_spec: tuple[str, str]):
    db.add(incident)
    await db.flush()
    rows = [_notice(incident, kind, state) for kind, state in notices_spec]
    db.add_all(rows)
    await db.flush()
    return rows


async def _row(db, row: NotificationOutbox) -> NotificationOutbox:
    return await db.scalar(
        select(NotificationOutbox)
        .where(NotificationOutbox.id == row.id)
        .execution_options(populate_existing=True)
    )


async def _claimed_ids(db) -> set[uuid.UUID]:
    return {claim.id for claim in await claim_notification_batch(db, "test-worker", now=NOW)}


async def test_open_notice_waits_while_the_sweep_still_owns_the_incident(pg_async_session):
    db = pg_async_session
    owned = _incident("MONTHLY_SLOT_GENERATION_FAILED", "RETRYING", sla=NOW + timedelta(hours=5))
    (notice,) = await _seed(db, owned, ("INCIDENT_OPEN", "PENDING"))

    assert notice.id not in await _claimed_ids(db)
    assert (await _row(db, notice)).state == "PENDING"


async def test_open_notice_goes_out_once_the_window_has_passed(pg_async_session):
    db = pg_async_session
    overdue = _incident("MONTHLY_SLOT_GENERATION_FAILED", "RETRYING", sla=NOW - timedelta(minutes=1))
    (notice,) = await _seed(db, overdue, ("INCIDENT_OPEN", "PENDING"))

    assert notice.id in await _claimed_ids(db)


async def test_generation_incidents_keep_their_morning_page(pg_async_session):
    db = pg_async_session
    generation = _incident("CONTENT_GENERATION_FAILED", "RETRYING", sla=NOW + timedelta(hours=5))
    (notice,) = await _seed(db, generation, ("INCIDENT_OPEN", "PENDING"))

    assert notice.id in await _claimed_ids(db)


async def test_notices_of_an_incident_closed_before_sending_are_settled(pg_async_session):
    db = pg_async_session
    closed = _incident("MONTHLY_SLOT_GENERATION_FAILED", "ACKNOWLEDGED")
    opened, recovered = await _seed(
        db, closed, ("INCIDENT_OPEN", "PENDING"), ("INCIDENT_RECOVERED", "PENDING")
    )

    claimed = await _claimed_ids(db)

    assert opened.id not in claimed and recovered.id not in claimed
    for row in (await _row(db, opened), await _row(db, recovered)):
        assert row.state == "FAILED"
        assert row.safe_error_code == RESOLVED_BEFORE_SEND_CODE
        assert row.next_attempt_at is None


async def test_a_recovery_follows_an_open_that_already_reached_slack(pg_async_session):
    db = pg_async_session
    recovered_incident = _incident("CONTENT_GENERATION_FAILED", "RECOVERED")
    _sent, recovered = await _seed(
        db, recovered_incident, ("INCIDENT_OPEN", "SENT"), ("INCIDENT_RECOVERED", "PENDING")
    )

    assert recovered.id in await _claimed_ids(db)
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_notice_settlement_postgres.py -v` Expected: FAIL — `ImportError: cannot import name 'RESOLVED_BEFORE_SEND_CODE'`.

- [ ] **Step 3: 최소 구현** — `backend/app/services/notification_store.py`

(a) import 수정:

```python
from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, aliased
...
from app.models.operations import (
    Incident,
    IncidentSeverity,
    IncidentState,
    JSONValue,
    NotificationOutbox,
    NotificationOutboxState,
)
...
from app.services.incident_types import (
    QUIET_INCIDENT_TYPES,
    SWEEP_OWNED_INCIDENT_TYPES,
    IncidentFingerprint,
    IncidentOpenRequest,
    incident_is_quiet,
)
```

(b) `_FALLBACK_TEXT_COLUMN_LIMIT` 아래에 상수 추가:

```python
# 사고가 알림 발송 전에 복구·확인됐다 — 보내지 않고 종결한다. 그 사고의 열림 알림이 하나도
# 나가지 않았다면 짝인 복구 알림도 보내지 않는다(나가지 않은 '조치 필요'에 '해결'은 필요 없다).
RESOLVED_BEFORE_SEND_CODE = "INCIDENT_RESOLVED_BEFORE_SEND"
_RESOLVED_BEFORE_SEND_MESSAGE = "사고가 알림 발송 전에 복구·확인되어 보내지 않았습니다."
_SENDABLE_STATES = (NotificationOutboxState.PENDING.value, NotificationOutboxState.RETRYING.value)
_MAY_HAVE_REACHED_STATES = (
    NotificationOutboxState.PENDING.value,
    NotificationOutboxState.SENDING.value,
    NotificationOutboxState.RETRYING.value,
    NotificationOutboxState.HOLD.value,
    NotificationOutboxState.SENT.value,
)
```

(c) `claimable_notifications_stmt`(`:338-366`)를 다음으로 바꾼다.

```python
def claimable_notifications_stmt(*, claimed_at: datetime, limit: int):
    """보낼 차례인 알림.

    - 조용한 사고 종류에 묶인 행은 집지 않는다(입구에서 막기 전에 쌓인 행 포함).
    - 스윕이 재시도를 소유하는 사고(`SWEEP_OWNED_INCIDENT_TYPES`)가 기한 안에서 RETRYING이면 그
      열림 알림을 집지 않는다. 기한이 지나면 그때 나간다 — 사람에게 넘어가는 순간의 알림이다.
    """

    quiet_incident = (
        select(Incident.id)
        .where(
            Incident.id == NotificationOutbox.incident_id,
            func.upper(Incident.incident_type).in_(sorted(QUIET_INCIDENT_TYPES)),
        )
        .exists()
    )
    machine_owned = (
        select(Incident.id)
        .where(
            Incident.id == NotificationOutbox.incident_id,
            func.upper(Incident.incident_type).in_(sorted(SWEEP_OWNED_INCIDENT_TYPES)),
            Incident.state == IncidentState.RETRYING.value,
            Incident.sla_due_at > claimed_at,
        )
        .exists()
    )
    return (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.state.in_(_SENDABLE_STATES),
            NotificationOutbox.next_attempt_at <= claimed_at,
            ~quiet_incident,
            ~and_(NotificationOutbox.notification_type == "INCIDENT_OPEN", machine_owned),
        )
        .order_by(
            NotificationOutbox.next_attempt_at,
            NotificationOutbox.created_at,
            NotificationOutbox.id,
        )
        .with_for_update(skip_locked=True)
        .limit(max(1, min(limit, 100)))
    )


async def settle_incident_notices(db: AsyncSession, *, now: datetime) -> None:
    """발송 전에 끝난 사고의 열림 알림, 그리고 열림이 한 번도 나가지 않은 복구 알림을 종결한다."""

    settled = {
        "state": NotificationOutboxState.FAILED.value,
        "next_attempt_at": None,
        "safe_error_code": RESOLVED_BEFORE_SEND_CODE,
        "safe_error_message": _RESOLVED_BEFORE_SEND_MESSAGE,
        "version": NotificationOutbox.version + 1,
        "updated_at": now,
    }
    resolved_incident = (
        select(Incident.id)
        .where(
            Incident.id == NotificationOutbox.incident_id,
            Incident.state.in_(
                (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
            ),
        )
        .exists()
    )
    await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.notification_type == "INCIDENT_OPEN",
            NotificationOutbox.state.in_(_SENDABLE_STATES),
            resolved_incident,
        )
        .values(**settled)
        .execution_options(synchronize_session=False)
    )
    suppressed = aliased(NotificationOutbox)
    reached = aliased(NotificationOutbox)
    open_was_suppressed = (
        select(suppressed.id)
        .where(
            suppressed.incident_id == NotificationOutbox.incident_id,
            suppressed.notification_type == "INCIDENT_OPEN",
            suppressed.safe_error_code == RESOLVED_BEFORE_SEND_CODE,
        )
        .exists()
    )
    open_may_have_reached = (
        select(reached.id)
        .where(
            reached.incident_id == NotificationOutbox.incident_id,
            reached.notification_type == "INCIDENT_OPEN",
            reached.state.in_(_MAY_HAVE_REACHED_STATES),
        )
        .exists()
    )
    await db.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.notification_type == "INCIDENT_RECOVERED",
            NotificationOutbox.state.in_(_SENDABLE_STATES),
            open_was_suppressed,
            ~open_may_have_reached,
        )
        .values(**settled)
        .execution_options(synchronize_session=False)
    )
```

(d) `claim_notification_batch`(`:395-398`)에서 행을 고르기 전에 한 줄 추가:

```python
    claimed_at = now or datetime.now(UTC)
    await settle_incident_notices(db, now=claimed_at)
    rows = list(
        (await db.execute(claimable_notifications_stmt(claimed_at=claimed_at, limit=limit))).scalars()
    )
```

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_notice_settlement_postgres.py tests/test_quiet_incident_notifications.py tests/test_notification_outbox.py -v` Expected: 모두 PASS. (`test_claim_query_never_picks_quiet_incident_notifications`는 SQL에 `POST_PUBLISH_REVIEW_FLAGGED`·`incidents`가 계속 있으므로 PASS.)

- [ ] **Step 5: 커밋** `git add backend/app/services/notification_store.py backend/tests/integration/test_incident_notice_settlement_postgres.py && git commit -m "fix(notifications): 기한 안의 스윕 소유 사고 열림 알림은 보류하고 발송 전 해결된 알림 짝은 종결한다"`

**계약 영향:** CLAUDE.md "Slack은 [알림 정책]을 따른다…" 절에 추가: "스윕이 재시도를 소유하는 사고(`INCIDENT_TYPE_POLICIES`의 sweep_owned)의 열림 알림은 그 기한까지 발송기가 보류하고, 기한 안에 복구·확인되면 `INCIDENT_RESOLVED_BEFORE_SEND`로 종결한다. 열림이 나가지 않은 사고의 복구 알림도 보내지 않는다." `docs/ops/slack-notification-policy.md`도 같은 문장.
**위험·롤백:** ① Admin 알림 목록에 `FAILED`(INCIDENT_RESOLVED_BEFORE_SEND) 행이 보인다 — 수동 재시도 대상처럼 보일 수 있다(선례 `STALE_NOT_RESENT`와 같은 모양). ② 상관 서브쿼리 UPDATE가 매 분 실행 — `ix_notification_outbox_claim`(PENDING/RETRYING 부분 색인)이 받는다. 롤백은 커밋 되돌리기; 이미 FAILED로 종결된 행은 남는다(전달 안 된 사실과 일치).

---

### Task 1-1b: `open_or_touch_incident`가 레지스트리를 따라 RETRYING+기한으로 연다

**Files:**
- Modify: `backend/app/services/incidents.py:8` (import), `:27-36` (import), `:51-149` (`open_or_touch_incident`)
- Test: `backend/tests/integration/test_incident_type_policy_postgres.py` (신규)

**조사 결과:**
- `incidents.py:72-93` INSERT는 `state=OPEN`, `sla_due_at` 미지정. 충돌 갱신(`:100-146`)도 `state=OPEN` 고정이며 `sla_due_at`을 건드리지 않는다. 에피소드 시작은 RECOVERED/ACKNOWLEDGED에서 다시 열 때만 `first_seen_at`을 바꾼다(`:130-139`) — 이것을 기한 계산의 에피소드 시작으로 쓴다.
- 이전 대상 4종의 호출자 모두 이 함수를 쓴다: 슬롯(`monthly_slot_incident_control.py:54`), 월간 측정(`weekly_sov_incident_control.py:207`), 두 디스패치 실패(`ops_incident_alerts.open_ops_incident` → `:63`).
- 호출자의 Slack 판정(`previous_state is None or in {RECOVERED, ACKNOWLEDGED}`)은 그대로 둔다. RETRYING으로 열려도 열림 알림이 outbox에 들어가고 1-4가 기한까지 보류한다. RETRYING→(기한 경과 터치)→OPEN일 때는 이전 상태가 RETRYING이라 새 행을 넣지 않지만, 같은 에피소드의 보류 행이 그때 풀려 나간다.
- `literal(..., type_=Incident.sla_due_at.type)`로 묶지 않으면 asyncpg가 `timestamptz`가 아닌 형식으로 바인딩할 수 있다(`DateTime(timezone=True)`).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/integration/test_incident_type_policy_postgres.py`

```python
"""스윕 소유 사고 종류는 RETRYING+기한으로 열린다(Phase 1-1). 등록 밖 종류는 종전과 같다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.operations import IncidentSeverity
from app.services.incident_types import IncidentFingerprint, IncidentOpenRequest
from app.services.incidents import open_or_touch_incident

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)  # 12:00 KST, 다음 달(11월) 슬롯 준비 기간


def _request(incident_type: str = "MONTHLY_SLOT_GENERATION_FAILED", period: str = "2026-11"):
    object_id = f"{uuid.uuid4()}:{period}"
    return IncidentOpenRequest(
        pipeline="policy_test",
        object_type="hospital_month",
        object_id=object_id,
        fingerprint=IncidentFingerprint.VALIDATION_FAILED,
        incident_type=incident_type,
        severity=IncidentSeverity.HIGH,
        customer_impact="테스트 영향",
        source_type="POLICY_TEST",
        next_action="테스트 조치",
        admin_path="/operations",
        source_id=object_id,
    )


async def _open(db, request, at):
    incident = await open_or_touch_incident(db, request, now=at)
    await db.flush()
    return incident


async def test_sweep_owned_type_opens_as_retrying_with_its_window(pg_async_session):
    incident = await _open(pg_async_session, _request(), NOW)

    assert incident.state == "RETRYING"
    assert incident.sla_due_at == NOW + timedelta(hours=24)


async def test_touch_inside_the_window_keeps_retrying_and_the_original_deadline(pg_async_session):
    request = _request()
    await _open(pg_async_session, request, NOW)

    touched = await _open(pg_async_session, request, NOW + timedelta(hours=6))

    assert touched.state == "RETRYING"
    assert touched.sla_due_at == NOW + timedelta(hours=24)
    assert touched.occurrence_count == 2


async def test_touch_after_the_window_hands_the_incident_to_people(pg_async_session):
    request = _request()
    await _open(pg_async_session, request, NOW)

    late = await _open(pg_async_session, request, NOW + timedelta(hours=25))

    assert late.state == "OPEN"


async def test_unregistered_type_keeps_todays_open_without_deadline(pg_async_session):
    incident = await _open(pg_async_session, _request("POLICY_TEST_UNREGISTERED"), NOW)

    assert incident.state == "OPEN"
    assert incident.sla_due_at is None


async def test_an_open_incident_is_never_demoted_by_a_later_touch(pg_async_session):
    request = _request()
    first = await _open(pg_async_session, request, NOW)
    first.state = "OPEN"  # 배포 전부터 사람이 가진 행
    await pg_async_session.flush()

    touched = await _open(pg_async_session, request, NOW + timedelta(hours=1))

    assert touched.state == "OPEN"


async def test_reopen_after_acknowledgement_starts_a_new_window(pg_async_session):
    request = _request()
    first = await _open(pg_async_session, request, NOW)
    first.state = "ACKNOWLEDGED"
    first.recovered_at = NOW
    first.acknowledged_at = NOW
    await pg_async_session.flush()

    reopened = await _open(pg_async_session, request, NOW + timedelta(days=2))

    assert reopened.state == "RETRYING"
    assert reopened.episode_seq == 2
    assert reopened.sla_due_at == NOW + timedelta(days=3)
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_type_policy_postgres.py -v` Expected: `test_sweep_owned_type_opens_as_retrying_with_its_window` 등 4건 FAIL — `AssertionError: assert 'OPEN' == 'RETRYING'`; 등록 밖·OPEN 유지 2건은 PASS.

- [ ] **Step 3: 최소 구현** — `backend/app/services/incidents.py`

(a) import:

```python
from sqlalchemy import case, func, literal, or_, select, update
...
from app.services.incident_types import (
    IncidentFilters,
    IncidentFingerprint,
    IncidentLabels,
    IncidentMutationResult,
    IncidentNotFound,
    IncidentOpenRequest,
    IncidentTransitionConflict,
    IncidentVersionConflict,
    incident_policy,
    retry_deadline,
)
```

(b) `open_or_touch_incident`(`:51-149`) 전체를 다음으로 바꾼다.

```python
async def open_or_touch_incident(
    db: AsyncSession,
    request: IncidentOpenRequest,
    *,
    actor: str = "system",
    reason: str = "failure observed",
    now: datetime | None = None,
) -> Incident:
    """Atomically create or reopen one logical incident and preserve its first fact.

    스윕이 재시도를 소유하는 종류(`incident_types.INCIDENT_TYPE_POLICIES`의 sweep_owned)는 기한이
    남아 있으면 RETRYING+기한으로 열린다 — 기한 안의 자동 재시도는 사람의 일이 아니다. 기한은
    에피소드 시작에서 계산하므로 터치마다 밀리지 않는다. 이미 OPEN인 행(사람이 가진 일)은 내리지
    않는다. 기한이 지났거나 정할 수 없으면 종전처럼 OPEN이다. 등록 밖 종류는 종전과 같다.
    """

    observed_at = now or datetime.now(UTC)
    # Every incident is, by definition, an exceptional operation.  Keep a durable
    # machine key and operator-safe explanation even when an older caller only
    # supplied the incident type/customer impact.  Operations grouping must never
    # have to infer its key from Korean display copy (FN-04).
    safe_error_code = normalize_incident_code(
        request.safe_error_code or request.incident_type
    )
    safe_error_message = sanitize_operator_text(
        request.safe_error_message or request.customer_impact
    ) or "운영 작업이 완료되지 않은 원인을 확인해야 합니다."
    dedupe_key = build_incident_key(
        request.pipeline, request.object_type, request.object_id, request.fingerprint
    )
    retry_until = await _sweep_retry_deadline(db, request, dedupe_key, observed_at)
    base = insert(Incident).values(
        id=uuid.uuid4(),
        hospital_id=request.hospital_id,
        operation_run_id=request.operation_run_id,
        dedupe_key=dedupe_key,
        incident_type=normalize_incident_code(request.incident_type),
        state=(
            IncidentState.RETRYING if retry_until is not None else IncidentState.OPEN
        ).value,
        sla_due_at=retry_until,
        severity=request.severity.value,
        customer_impact=sanitize_operator_text(request.customer_impact) or "고객 영향 정보를 아직 확인하지 못했습니다.",
        source_type=normalize_incident_code(request.source_type),
        source_id=normalize_source_id(request.source_id),
        safe_error_code=safe_error_code,
        safe_error_message=safe_error_message,
        next_action=sanitize_operator_text(request.next_action) or "상세 화면에서 원인을 확인하고, 조치 버튼이 없으면 개발팀 문의용 정보를 전달하세요.",
        admin_path=normalize_admin_path(request.admin_path),
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        created_at=observed_at,
        updated_at=observed_at,
    )
    severity = case(
        (or_(Incident.severity == "CRITICAL", base.excluded.severity == "CRITICAL"), "CRITICAL"),
        (or_(Incident.severity == "HIGH", base.excluded.severity == "HIGH"), "HIGH"),
        (or_(Incident.severity == "MEDIUM", base.excluded.severity == "MEDIUM"), "MEDIUM"),
        else_="LOW",
    )
    reopened = Incident.state.in_(
        (IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value)
    )
    touch: dict[str, object] = {
        "state": _touched_state(retry_until),
        "severity": severity,
        "operation_run_id": func.coalesce(
            base.excluded.operation_run_id, Incident.operation_run_id
        ),
        "customer_impact": base.excluded.customer_impact,
        "safe_error_code": func.coalesce(
            base.excluded.safe_error_code, Incident.safe_error_code
        ),
        "safe_error_message": func.coalesce(
            base.excluded.safe_error_message, Incident.safe_error_message
        ),
        "next_action": base.excluded.next_action,
        "admin_path": base.excluded.admin_path,
        "last_seen_at": observed_at,
        "occurrence_count": Incident.occurrence_count + 1,
        "episode_seq": case((reopened, Incident.episode_seq + 1), else_=Incident.episode_seq),
        # Episode start. Reset only on reopen so 48h promotion can fire again.
        "first_seen_at": case((reopened, observed_at), else_=Incident.first_seen_at),
        "recovered_at": None,
        "acknowledged_at": None,
        "acknowledged_by_id": None,
        "version": Incident.version + 1,
        "updated_at": observed_at,
    }
    if retry_until is not None:
        # 사람이 가진 OPEN의 기한은 건드리지 않는다. 스윕이 가진 행만 기한을 맞춘다.
        touch["sla_due_at"] = case(
            (Incident.state == IncidentState.OPEN.value, Incident.sla_due_at),
            else_=literal(retry_until, type_=Incident.sla_due_at.type),
        )
    statement = base.on_conflict_do_update(
        index_elements=[Incident.dedupe_key], set_=touch
    ).returning(Incident).execution_options(populate_existing=True)
    incident = (await db.execute(statement)).scalar_one()
    await _audit(db, incident, actor, "incident_occurrence_recorded", reason)
    return await auto_assign_owner(db, incident, observed_at=observed_at, actor=actor)


async def _sweep_retry_deadline(
    db: AsyncSession, request: IncidentOpenRequest, dedupe_key: str, observed_at: datetime
) -> datetime | None:
    """스윕 소유 종류면 이 에피소드의 남은 기한. 아니면 None(한 번의 조회도 하지 않는다)."""

    if not incident_policy(request.incident_type).sweep_owned:
        return None
    existing = (
        await db.execute(
            select(Incident.state, Incident.first_seen_at).where(
                Incident.dedupe_key == dedupe_key
            )
        )
    ).one_or_none()
    live = existing is not None and existing.state in (
        IncidentState.OPEN.value,
        IncidentState.RETRYING.value,
    )
    episode_start = existing.first_seen_at if live else observed_at
    return retry_deadline(request, episode_start=episode_start, observed_at=observed_at)


def _touched_state(retry_until: datetime | None):
    if retry_until is None:
        return IncidentState.OPEN.value
    return case(
        (Incident.state == IncidentState.OPEN.value, IncidentState.OPEN.value),
        else_=IncidentState.RETRYING.value,
    )
```

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_type_policy_postgres.py tests/integration/test_incident_backlog_postgres.py tests/test_admin_operations.py tests/test_operations_actionability.py -v` Expected: 모두 PASS.

- [ ] **Step 5: 커밋** `git add backend/app/services/incidents.py backend/tests/integration/test_incident_type_policy_postgres.py && git commit -m "feat(incidents): 스윕 소유 사고 종류는 RETRYING+기한으로 열고 기한이 지나야 사람에게 넘긴다"`

**계약 영향:** CLAUDE.md "자율 작업·알림·비용" 절의 "자동 복구가 예산 안에서 소유한 상태(RETRYING)는 운영자 큐·현황 카드·Slack에 사람의 일로 올리지 않는다." 뒤에 추가: "스윕이 재시도를 소유하는 사고 종류(월간 슬롯·창이 열린 월간 측정·사이트/콘텐츠 큐 투입 실패·예산이 남은 Essence 보류)는 `incident_types.INCIDENT_TYPE_POLICIES`에 등록하며 처음부터 RETRYING+기한으로 열린다. 기한은 에피소드 시작에서 계산하고 지나면 OPEN이다. 등록 밖 종류는 OPEN으로 연다."
**위험·롤백:** ① 월간 측정 실패는 창이 열린 24일에 생기면 다음 달 7일까지 최대 14일 RETRYING이다(감사 의도대로지만 결정적 실패라도 그동안 사람의 일이 아니다 — 1~7일 공백 요약이 별도로 알린다). 대표 확인 질문으로 올린다. ② 등록 종류의 기존 OPEN 행은 그대로 OPEN — 배포 후 정리는 1-2 resolver가 맡는다. 롤백은 커밋 되돌리기(이미 RETRYING인 행은 1-3 기본 기한이 지나면 사람의 일이 된다).

---

### Task 1-1c: Essence 보류를 처음부터 RETRYING으로 연다 (1-4 후반부)

**Files:**
- Modify: `backend/app/services/ops_incident_alerts.py:36-84` (`open_ops_incident`에 `retry_until`)
- Modify: `backend/app/workers/tasks.py:3921-3967` (함수 삭제), `:4081-4112` (호출)
- Modify test: `backend/tests/test_essence_auto_review_dispatch.py:39,60-64,290-330`

**조사 결과:**
- 지금은 `open_ops_incident`가 OPEN으로 열고 같은 트랜잭션에서 INCIDENT_OPEN을 넣은 뒤 커밋(`ops_incident_alerts.py:85-117`), 그 다음 별도 세션에서 `_defer_essence_escalation_to_automatic_retry`가 OPEN→RETRYING으로 바꾼다(`tasks.py:3921-3967`). 발송기가 그 사이 OPEN을 보내므로 자동 재검수 1회차부터 Error 알림이 나간다 — 감사 F3 확인.
- `EssenceRefreshResult.automatic_recovery_exhausted`는 `next_automatic_attempt_at is None`(`essence_auto_review.py:285-288`).
- 두 번째 보류(사이클 2)에서 `previous_state`가 RETRYING이라 새 알림을 넣지 않는다. 소진 시 `retry_until=None` → 1-1b에 따라 OPEN이 되고, 첫 에피소드의 보류 중인 OPEN 행이 1-4에 따라 그때 나간다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_essence_auto_review_dispatch.py`

(a) `_run_essence_task`의 `monkeypatch.setattr(tasks, "_defer_essence_escalation_to_automatic_retry", ...)` 블록(`:60-64`)을 지운다(함수를 없애므로). `deferred` 리스트는 빈 채로 돌려준다.

(b) `test_escalation_with_recovery_budget_left_is_not_operator_work`의 마지막 단언을 바꾼다.

```python
    # 시도마다 새 인시던트를 열지 않는다 — 같은 병원·snapshot 한 건을 만지기만 한다.
    assert len(opened) == 1
    assert opened[0]["object_id"] == f"{hospital_id}:current-snapshot"
    # 처음부터 RETRYING+다음 자동 재검수 시각으로 연다(OPEN→Slack→RETRYING 경합 없음).
    assert opened[0]["retry_until"] == retry_at
    assert opened[0]["severity"] == IncidentSeverity.MEDIUM
    assert deferred == []
```

(c) `test_exhausted_escalation_becomes_operator_work`:

```python
    assert len(opened) == 1
    assert opened[0]["retry_until"] is None
    assert opened[0]["severity"] == IncidentSeverity.HIGH
    assert deferred == []
```

파일 상단 import에 `from app.models.operations import IncidentSeverity`가 없으면 추가한다.

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_essence_auto_review_dispatch.py -v` Expected: 두 테스트 FAIL — `KeyError: 'retry_until'`.

- [ ] **Step 3: 최소 구현**

(a) `ops_incident_alerts.py` `open_ops_incident` 시그니처에 `retry_until: datetime | None = None,`(`severity` 다음)를 더하고 `IncidentOpenRequest(...)`에 `retry_until=retry_until,`를 넘긴다. docstring 끝에: "`retry_until`은 스윕 소유 종류에서 다음 자동 시도 시각이다 — 그때까지 RETRYING이고 열림 알림은 발송기가 보류한다."

(b) `tasks.py:3921-3967` `_defer_essence_escalation_to_automatic_retry` 함수 전체 삭제.

(c) `tasks.py:4081-4112`를 다음으로 바꾼다.

```python
    if result.requires_operator and result.previous_philosophy_id is None:
        snapshot = result.snapshot_hash or "unknown"
        # 예산이 남은 보류는 처음부터 RETRYING+다음 자동 재검수 시각으로 연다. 열림 알림은
        # 발송기가 그 시각까지 보류하고, 그 안에 승인되면 보내지 않는다. 소진되면 OPEN이다.
        retry_until = (
            None
            if result.automatic_recovery_exhausted
            else result.next_automatic_attempt_at
        )
        _run_async(
            open_ops_incident(
                pipeline="essence_auto_review",
                object_type="essence_snapshot",
                object_id=f"{hospital_id}:{snapshot}",
                incident_type="ESSENCE_AUTO_REVIEW_ESCALATED",
                safe_error_code="ESSENCE_AUTO_REVIEW_ESCALATED",
                problem=(
                    result.findings[0]
                    if result.findings
                    else "AI 근거 검수가 자동 승인을 보류했습니다."
                ),
                customer_impact="승인 운영 기준이 없어 콘텐츠 생성과 발행이 중지되며 초안은 검토 대기 상태로 보관됩니다.",
                next_action="운영센터에서 새 운영 기준 초안의 근거와 충돌 항목만 확인해 주세요.",
                source_type="ESSENCE_AUTO_REVIEW",
                hospital_name=hospital_name,
                hospital_id=hospital_uuid,
                admin_path=f"/hospitals/{hospital_id}",
                fingerprint=IncidentFingerprint.VALIDATION_FAILED,
                actor=AUTO_ESSENCE_ACTOR,
                retry_until=retry_until,
                severity=(
                    IncidentSeverity.MEDIUM if retry_until is not None else IncidentSeverity.HIGH
                ),
            )
        )
```

`tasks.py`에서 `mark_retrying`·`build_incident_key`의 지역 import가 이 함수에만 있었으므로 함께 사라진다. 다른 곳에서 `_defer_essence_escalation_to_automatic_retry`를 부르지 않는지 `grep -rn _defer_essence_escalation app tests`로 확인(결과 0).

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_essence_auto_review_dispatch.py tests/integration/test_essence_auto_review_postgres.py -v` Expected: 모두 PASS.

- [ ] **Step 5: 커밋** `git add backend/app/services/ops_incident_alerts.py backend/app/workers/tasks.py backend/tests/test_essence_auto_review_dispatch.py && git commit -m "fix(essence): 예산이 남은 Essence 보류를 처음부터 RETRYING으로 열어 1회차 Error 알림을 없앤다"`

**계약 영향:** CLAUDE.md "Essence 자동 검수가 보류하면 … 최대 4회 자동 재검수한다." 뒤에 "그동안 보류 사고는 RETRYING+다음 재검수 시각이며 열림 알림은 소진 전까지 나가지 않는다."
**위험·롤백:** `next_automatic_attempt_at`이 과거면 1-1b가 곧장 OPEN으로 연다(종전엔 RETRYING+과거 기한 = 사실상 같음). 롤백은 커밋 되돌리기.

---

### Task 1-3: 기한 없는 RETRYING에 기본 기한 24시간

**Files:**
- Modify: `backend/app/services/incidents.py:211-217` (`mark_retrying`), `:290-298` (`_transition` 타입힌트)
- Modify: `backend/app/workers/incident_backlog.py` (끝에 `bound_unowned_retrying`, `settle_incidents`)
- Modify: `backend/app/workers/autonomous_recovery.py:49,291`
- Modify test stubs: `backend/tests/test_autonomous_loop_recovery.py:24-26`, `backend/tests/integration/test_site_recovery_postgres.py:90-92`
- Test: `backend/tests/integration/test_incident_retry_deadline_postgres.py` (신규)

**조사 결과:**
- `requires_operator_action`(`operator_action.py:19`)은 `sla_due_at is None`인 RETRYING을 영원히 사람의 일로 보지 않는다. 일일 요약도 SQL `Incident.sla_due_at < now`(`fleet_heartbeat.py:306`)가 NULL이면 거짓이라 "자동 복구 진행"으로만 센다.
- `sla_due_at`을 남기지 않고 RETRYING을 **유지하는** 호출자: `lead_recovery_incidents.py:66-81`(복구 실행 동안), `naver_handoff_incidents.py:77-90`(재수집 동안), `content_publish_delivery.py:159-166`(OPEN→RETRYING 후 다음 전달 관측까지), `generation_incident_control.py:1189-1202`(MISSING_APPROVED_ESSENCE, **의도적으로 None** — "Human action is represented by the separate snapshot-keyed ESCALATED incident"). 나머지 `mark_retrying` 호출(약 15곳)은 같은 트랜잭션에서 곧장 RECOVERED로 넘어간다.
- **감사와 다른 결정**: 술어에 `updated_at`을 넣어 24시간을 강제하면 ① 8곳 호출부(`operations_center_*`, `content.py:2560`, `fleet_heartbeat.py:345` SQL 등) 시그니처를 바꿔야 하고 ② MISSING_APPROVED_ESSENCE를 ESCALATED 사고와 이중으로 센다. 그래서 쓰기 쪽(`mark_retrying`이 기본 기한을 단다) + 기존 행 정리 스윕(매 분 `reconcile`)으로 한다. 술어는 하나 그대로다.
- `mark_retrying` 호출자가 반환 행에 기한을 덮어쓰는 패턴(`generation_incident_control.py:1218-1225`, 종전 Essence)은 그대로 동작한다(ORM 속성 대입이 flush에서 이긴다).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/integration/test_incident_retry_deadline_postgres.py`

```python
"""주인 없는 '복구 중'을 없앤다 — 기한 없는 RETRYING은 24시간 뒤 사람의 일(Phase 1-3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.operations import Incident
from app.services.incidents import mark_retrying
from app.workers.incident_backlog import settle_incidents

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def _incident(state="OPEN", *, sla=None, incident_type="NAVER_SOURCE_FETCH_FAILED", code=None, updated=None):
    return Incident(
        dedupe_key=f"deadline-test:{uuid.uuid4()}",
        incident_type=incident_type,
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type="TEST",
        next_action="테스트",
        admin_path="/operations",
        sla_due_at=sla,
        safe_error_code=code,
        first_seen_at=NOW - timedelta(days=3),
        last_seen_at=NOW - timedelta(days=3),
        updated_at=updated or NOW - timedelta(days=3),
    )


async def _retry(db, incident):
    db.add(incident)
    await db.flush()
    return await mark_retrying(
        db, incident.id, expected_version=incident.version, actor="test", reason="test", now=NOW
    )


async def test_retrying_without_a_deadline_gets_the_default_window(pg_async_session):
    result = await _retry(pg_async_session, _incident())

    assert result.state == "RETRYING"
    assert result.sla_due_at == NOW + timedelta(hours=24)


async def test_a_future_deadline_is_kept(pg_async_session):
    due = NOW + timedelta(hours=6)
    result = await _retry(pg_async_session, _incident(sla=due))

    assert result.sla_due_at == due


async def test_a_past_deadline_from_an_old_episode_is_replaced(pg_async_session):
    result = await _retry(pg_async_session, _incident(sla=NOW - timedelta(days=10)))

    assert result.sla_due_at == NOW + timedelta(hours=24)


def test_existing_unbounded_retrying_rows_get_a_deadline_from_their_last_change(pg_conn):
    db = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    stuck = _incident("RETRYING", updated=NOW - timedelta(days=2))
    owned_elsewhere = _incident(
        "RETRYING", incident_type="CONTENT_GENERATION_FAILED", code="MISSING_APPROVED_ESSENCE"
    )
    db.add_all([stuck, owned_elsewhere])
    db.flush()

    settle_incidents(db, now=NOW)

    db.expire_all()
    assert db.get(Incident, stuck.id).sla_due_at == NOW - timedelta(days=1)
    # 사람의 일을 별도 ESCALATED 사고가 가진 생성 보류는 기한 없이 둔다.
    assert db.get(Incident, owned_elsewhere.id).sla_due_at is None
    actions = db.scalars(
        select(AdminAuditLog.action).where(AdminAuditLog.target_id == str(stuck.id))
    ).all()
    assert "incident_retry_deadline_defaulted" in actions
    db.close()
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_retry_deadline_postgres.py -v` Expected: FAIL — `ImportError: cannot import name 'settle_incidents'`; (import를 임시로 지우면) `mark_retrying() got an unexpected keyword argument 'now'`.

- [ ] **Step 3: 최소 구현**

(a) `incidents.py` import에 `DEFAULT_RETRYING_DEADLINE` 추가, `mark_retrying`(`:211-217`) 교체:

```python
async def mark_retrying(
    db: AsyncSession,
    incident_id: uuid.UUID,
    *,
    expected_version: int,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> IncidentMutationResult:
    """OPEN→RETRYING. 기한 없는 RETRYING은 주인 없는 '복구 중'이므로 기본 기한(24시간)을 단다.

    미래 기한이 이미 있으면 그대로 둔다. 호출자는 돌려받은 행에 더 정확한 기한을 덮어쓸 수 있고,
    사람의 일을 다른 사고가 가진 경우(MISSING_APPROVED_ESSENCE)는 None으로 지울 수 있다.
    """

    changed_at = now or datetime.now(UTC)
    deadline = case(
        (Incident.sla_due_at > changed_at, Incident.sla_due_at),
        else_=literal(changed_at + DEFAULT_RETRYING_DEADLINE, type_=Incident.sla_due_at.type),
    )
    return await _transition(
        db, incident_id, expected_version, IncidentState.OPEN, IncidentState.RETRYING,
        actor, "incident_retrying", reason, {"sla_due_at": deadline}, changed_at,
    )
```

`_transition`·`_mutate`의 `extra`/`values` 타입힌트를 `dict[str, object]`로 넓힌다(`:293`, `:303`).

(b) `incident_backlog.py` 끝에 추가(import: `from sqlalchemy import ... and_, or_` 이미 있음; `from app.services.incident_types import DEFAULT_RETRYING_DEADLINE`):

```python
def bound_unowned_retrying(
    db: Session, *, now: datetime, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """기한 없이 남은 RETRYING에 마지막 변경 + 24시간 기한을 단다(배포 전 행·옛 호출자).

    MISSING_APPROVED_ESSENCE 생성 보류는 사람의 일을 별도 ESCALATED 사고가 가지므로 제외한다.
    """

    rows = list(
        db.execute(
            select(Incident)
            .where(
                Incident.state == IncidentState.RETRYING.value,
                Incident.sla_due_at.is_(None),
                or_(
                    Incident.incident_type != "CONTENT_GENERATION_FAILED",
                    Incident.safe_error_code.is_distinct_from("MISSING_APPROVED_ESSENCE"),
                ),
            )
            .order_by(Incident.updated_at, Incident.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for incident in rows:
        incident.sla_due_at = incident.updated_at + DEFAULT_RETRYING_DEADLINE
        incident.version += 1
        incident.updated_at = now
        _audit(
            db,
            incident,
            "incident_retry_deadline_defaulted",
            detail_extra={"sla_due_at": incident.sla_due_at.isoformat()},
        )
    db.flush()
    return len(rows)


def settle_incidents(
    db: Session, *, now: datetime | None = None, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """자동화가 끝낸 일을 사람의 일로 남기지 않는 정리. 커밋은 호출자가 한다."""

    observed = now or datetime.now(UTC)
    return bound_unowned_retrying(db, now=observed, limit=limit)
```

(c) `autonomous_recovery.py:49` import를 `from app.workers.incident_backlog import close_resolved_backlog_incidents, settle_incidents`로, `:291`을 다음으로:

```python
        resolved_task_incidents = close_resolved_backlog_incidents(db) + settle_incidents(db)
```

(d) 테스트 스텁 두 곳(`tests/test_autonomous_loop_recovery.py:26`, `tests/integration/test_site_recovery_postgres.py:90-92`) 바로 아래에 같은 모양으로 한 줄씩 추가:

```python
    monkeypatch.setattr(autonomous_recovery, "settle_incidents", lambda _db: 0)
```

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_retry_deadline_postgres.py tests/test_autonomous_loop_recovery.py tests/integration/test_site_recovery_postgres.py tests/integration/test_lead_recovery_incident_backlog_postgres.py -v` Expected: 모두 PASS.

- [ ] **Step 5: 커밋** `git add backend/app/services/incidents.py backend/app/workers/incident_backlog.py backend/app/workers/autonomous_recovery.py backend/tests/integration/test_incident_retry_deadline_postgres.py backend/tests/test_autonomous_loop_recovery.py backend/tests/integration/test_site_recovery_postgres.py && git commit -m "fix(incidents): 기한 없는 RETRYING에 24시간 기본 기한을 달아 주인 없는 복구 중을 없앤다"`

**계약 영향:** CLAUDE.md "사람의 일 건수는 `operator_action.is_operator_todo` 하나로 센다" 절에 "RETRYING은 언제나 기한을 갖는다 — `mark_retrying`이 기본 24시간을 달고, 정리 스윕이 옛 행에 마지막 변경 + 24시간을 단다(사람의 일을 다른 사고가 가진 MISSING_APPROVED_ESSENCE 생성 보류만 예외)."
**위험·롤백:** 배포 직후 오래된 RETRYING이 한꺼번에 기한 초과 → "확인 필요"가 일시 증가(감사가 예상한 위험). 1-2·1-5가 같은 배포에서 근거 있는 것을 닫으므로 순증은 근거 없는 것뿐이다. 롤백은 커밋 되돌리기(이미 단 기한은 남는다 — `UPDATE incidents SET sla_due_at=NULL WHERE id IN (감사 로그 incident_retry_deadline_defaulted 대상)`으로 되돌릴 수 있다).

---

### Task 1-2: 영구 OPEN 종류에 닫는 근거(resolver) 추가

**Files:**
- Modify: `backend/app/workers/incident_backlog.py:13-39` (import), `:225-259` (`_lead_diagnosis_gone`), `:305-322` (`_lead_report_recovered`), `:333-344` (`RESOLVERS`), `:347-408` (`close_resolved_backlog_incidents`)
- Test: `backend/tests/integration/test_incident_backlog_resolvers_postgres.py` (신규)

**조사 결과 (규칙별 근거와 저장 형태):**
- 인터페이스: `Resolver = Callable[[Session, Incident, datetime], str | None]`(`:44`). 닫기는 OPEN→RETRYING→RECOVERED→ACKNOWLEDGED, Slack 없이 감사 `incident_resolved_by_later_evidence`(`:380-406`). 대상은 OPEN만(`:357`). 스윕 소유 종류는 이제 RETRYING으로 열리므로 **sweep_owned ∩ RESOLVERS 종류는 RETRYING도 대상**으로 넓힌다(그 외 RETRYING은 종전처럼 건드리지 않음 — `test_retrying_incidents_belong_to_automation` 유지).
- `LEAD_DIAGNOSIS_FAILED`·`LEAD_DIAGNOSIS_RETRIES_EXHAUSTED`(`lead_diagnosis_tasks.py:305-318`, `:816-830`): `open_ops_incident` → `source_type=LEAD_DIAGNOSIS`, `source_id=진단 id`. 기존 `_lead_measurement_recovered`(`:262-270`)를 그대로 쓴다(갈음·파기·삭제 또는 실행 SUCCEEDED/PARTIAL).
- `LEAD_REPORT_BLOCKED`(`:518-534`): `source_type=LEAD_REPORT`. `_lead_diagnosis_gone`이 `LEAD_DIAGNOSIS`만 받으므로(`:241`) 출처 종류를 인자로 받게 고친다. 근거 = READY + 이 차단 뒤 만든 파기 안 된 산출물(`_lead_report_recovered`와 같은 규칙).
- `LEAD_DELIVERY_ABANDONED`(`:801-813`, **감사 1-2 목록에 없음, F4에는 있음**): `source_id=LeadDelivery.id`. 근거 = 그 행이 없음(리드 파기 CASCADE, `models/lead_diagnosis.py:388-393`) 또는 그 행이 SENT 또는 같은 진단의 나중 행이 SENT.
- `ESSENCE_MUST_USE_EXCLUDED`(`must_use_exclusions.py:115-155`): `source_id=승인본 id`, 승인본 버전당 한 건이며 이미 기록한 키는 상태와 무관하게 다시 열지 않는다(`:136-138`). 근거 = 그 승인본이 더 이상 APPROVED가 아님(병원당 APPROVED 하나, `models/essence.py:213-218` → 새 버전 승인 시 옛것은 ARCHIVED).
- `NAVER_SOURCE_FETCH_FAILED`(`naver_handoff_incidents.py:42-73`): `source_id=url_hash`(sha256 hex 64자, `normalize_source_id` 통과). 수집 실행은 `request_payload.source_type="NAVER_BLOG"`, `result_summary.items[*]={url_hash,state}`(`naver_handoff_runs.py:55-80,184-185`). 근거 = 그 병원의 더 나중 실행에서 같은 해시가 INGESTED 또는 SKIPPED(이미 저장된 중복). JSON 열은 `JSON().with_variant(JSONB)`라 `.contains`를 쓰려면 `cast(..., JSONB)`가 필요하다.
- `BROKER_UNAVAILABLE`(`operation_runs.py:295-316`): dedupe가 (operation_type, 병원)이고 `operation_run_id`는 마지막 실패 실행. 근거 = 같은 종류·같은 병원의 실행이 마지막 관측 뒤 `queued_at`을 가짐(브로커가 받음).
- `SITE_BUILD_DISPATCH_FAILED`(`tasks.py:4958-4979`): V0 뒤 "멱등 복구 신호"로 한 번 더 보낸 것(`:4914-4918`). 사이트 준비는 `reconcile`이 매 분 `profile_complete && !site_built` 병원에 `REBUILD_SITE`를 만든다(`autonomous_recovery.py:176-198, 284-289`). 근거 = `site_built` 또는 이 사고 뒤 `REBUILD_SITE` 실행이 생김(스윕이 소유; 소진은 `SITE_BUILD_RETRIES_EXHAUSTED`가 따로 연다).
- `CONTENT_DISPATCH_FAILED`(`content.py:517-535`): 오늘·내일 슬롯의 즉시 생성 투입 실패. 근거 = 그 이틀 슬롯에 미작성 DRAFT/READY가 없음, 또는 24시간 경과(글 단위 생성 사고가 소유).
- `MONTHLY_SOV_COHORT_OVER_LIMIT`(`monthly_cohort_incidents.py:66-102`): `source_id="YYYY-MM"`. 근거 = 그 달 종료(KST).
- `UNVERIFIED_ADMIN_ACTOR`(`core/security.py:271-296`): `source_id=actor`. 근거 = 30일 동안 재발 없음.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/integration/test_incident_backlog_resolvers_postgres.py`

```python
"""영구 OPEN이던 사고 종류를 DB 근거로 닫고, 근거가 없으면 열어 둔다(Phase 1-2)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.models.lead import SalesLead
from app.models.lead_diagnosis import LeadDelivery, LeadDiagnosis, LeadReportArtifact
from app.models.operations import Incident, OperationRun
from app.workers.incident_backlog import close_resolved_backlog_incidents

NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _hospital(db, **fields) -> Hospital:
    hospital = Hospital(
        name="사고해결가상의원",
        slug=f"resolver-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ACTIVE,
        **fields,
    )
    db.add(hospital)
    db.flush()
    return hospital


def _incident(
    db, incident_type, *, hospital=None, source_type="TEST", source_id=None,
    state="OPEN", seen=NOW - timedelta(days=30), run=None, sla=None,
):
    incident = Incident(
        hospital_id=hospital.id if hospital else None,
        operation_run_id=run.id if run else None,
        dedupe_key=f"resolver-test:{uuid.uuid4()}",
        incident_type=incident_type,
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type=source_type,
        source_id=source_id,
        next_action="테스트",
        admin_path="/operations",
        sla_due_at=sla,
        first_seen_at=seen,
        last_seen_at=seen,
    )
    db.add(incident)
    db.flush()
    return incident


def _state(db, incident) -> str:
    db.expire_all()
    return db.get(Incident, incident.id).state


def _diagnosis(db, **status):
    lead = SalesLead(
        clinic_name="사고해결가상의원", clinic_type="도입문의", contact="010-0000-0000",
        privacy=False, source="INQUIRY",
    )
    db.add(lead)
    db.flush()
    diagnosis = LeadDiagnosis(
        lead_id=lead.id,
        subject_hospital_name="사고해결가상의원",
        subject_region="수서역",
        queries=[{"slot": 1, "kind": "진료과형", "text": "수서역 근처 내과 병원 추천해줘"}],
        requested_models={"openai": "m", "gemini": "g", "judge": "j"},
        repeat_count=3,
        **status,
    )
    db.add(diagnosis)
    db.flush()
    return lead, diagnosis


@pytest.mark.parametrize("kind", ["LEAD_DIAGNOSIS_FAILED", "LEAD_DIAGNOSIS_RETRIES_EXHAUSTED"])
def test_lead_measurement_failure_closes_once_that_diagnosis_measured(db, kind):
    _lead, done = _diagnosis(db, execution_status="SUCCEEDED")
    _lead2, still = _diagnosis(db, execution_status="FAILED")
    closable = _incident(db, kind, source_type="LEAD_DIAGNOSIS", source_id=str(done.id))
    open_one = _incident(db, kind, source_type="LEAD_DIAGNOSIS", source_id=str(still.id))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
    assert _state(db, open_one) == "OPEN"


def test_lead_report_block_closes_once_a_servable_report_is_built_after_it(db):
    _lead, diagnosis = _diagnosis(db, execution_status="SUCCEEDED", report_status="READY")
    incident = _incident(
        db, "LEAD_REPORT_BLOCKED", source_type="LEAD_REPORT", source_id=str(diagnosis.id)
    )
    db.add(
        LeadReportArtifact(
            diagnosis_id=diagnosis.id, version=1, created_at=NOW - timedelta(days=1),
            storage_uri=f"gs://test-bucket/lead-reports/{diagnosis.id}.pdf",
            content_hash="0" * 64, byte_size=1024, template_version="lead-v1",
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_abandoned_delivery_closes_once_a_later_delivery_was_sent(db):
    lead, diagnosis = _diagnosis(db, execution_status="SUCCEEDED", report_status="READY")
    abandoned = LeadDelivery(lead_id=lead.id, diagnosis_id=diagnosis.id, event="REPORT", status="SENDING")
    db.add(abandoned)
    db.flush()
    incident = _incident(db, "LEAD_DELIVERY_ABANDONED", source_type="LEAD_DELIVERY", source_id=str(abandoned.id))
    db.add(
        LeadDelivery(
            lead_id=lead.id, diagnosis_id=diagnosis.id, event="REPORT", status="SENT",
            attempt=2, sent_at=NOW - timedelta(days=1), created_at=NOW - timedelta(days=1),
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_must_use_exclusion_closes_once_that_version_is_no_longer_approved(db):
    replaced = _hospital(db)
    current = _hospital(db)
    old = HospitalContentPhilosophy(hospital_id=replaced.id, version=1, status=PhilosophyStatus.ARCHIVED)
    live = HospitalContentPhilosophy(hospital_id=current.id, version=1, status=PhilosophyStatus.APPROVED)
    db.add_all([old, live])
    db.flush()
    closable = _incident(db, "ESSENCE_MUST_USE_EXCLUDED", hospital=replaced, source_id=str(old.id))
    still = _incident(db, "ESSENCE_MUST_USE_EXCLUDED", hospital=current, source_id=str(live.id))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
    assert _state(db, still) == "OPEN"


def test_naver_failure_closes_once_a_later_run_stored_that_post(db):
    hospital = _hospital(db)
    url_hash = "a" * 64
    incident = _incident(db, "NAVER_SOURCE_FETCH_FAILED", hospital=hospital, source_id=url_hash)
    db.add(
        OperationRun(
            hospital_id=hospital.id, operation_type="NAVER_SOURCE_SYNC", state="SUCCEEDED",
            request_payload={"source_type": "NAVER_BLOG", "url_hashes": [url_hash]},
            result_summary={"items": [{"url_hash": url_hash, "state": "INGESTED"}]},
            requested_at=NOW - timedelta(days=1),
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_broker_failure_closes_once_the_same_work_reached_the_queue(db):
    hospital = _hospital(db)
    failed = OperationRun(
        hospital_id=hospital.id, operation_type="REBUILD_SITE", state="FAILED",
        request_payload={}, requested_at=NOW - timedelta(days=30), safe_error_code="BROKER_UNAVAILABLE",
    )
    db.add(failed)
    db.flush()
    incident = _incident(db, "BROKER_UNAVAILABLE", hospital=hospital, run=failed, source_id=str(failed.id))
    db.add(
        OperationRun(
            hospital_id=hospital.id, operation_type="REBUILD_SITE", state="QUEUED",
            request_payload={}, requested_at=NOW - timedelta(days=1), queued_at=NOW - timedelta(days=1),
        )
    )
    db.flush()

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incident) == "ACKNOWLEDGED"


def test_site_dispatch_failure_closes_once_the_site_is_built_even_while_retrying(db):
    built = _hospital(db, site_built=True)
    waiting = _hospital(db, site_built=False)
    closable = _incident(
        db, "SITE_BUILD_DISPATCH_FAILED", hospital=built, state="RETRYING", sla=NOW + timedelta(hours=5)
    )
    still = _incident(
        db, "SITE_BUILD_DISPATCH_FAILED", hospital=waiting, state="RETRYING", sla=NOW + timedelta(hours=5)
    )

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, closable) == "ACKNOWLEDGED"
    assert _state(db, still) == "RETRYING"


def test_content_dispatch_failure_waits_for_near_slots_then_hands_over_after_a_day(db):
    hospital = _hospital(db)
    schedule = ContentSchedule(hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=date(2026, 10, 1))
    db.add(schedule)
    db.flush()
    db.add(
        ContentItem(
            hospital_id=hospital.id, schedule_id=schedule.id, content_type=ContentType.DISEASE,
            sequence_no=1, total_count=12, scheduled_date=date(2026, 10, 2), status=ContentStatus.DRAFT,
        )
    )
    db.flush()
    fresh = _incident(db, "CONTENT_DISPATCH_FAILED", hospital=hospital, state="RETRYING",
                      seen=NOW - timedelta(hours=2), sla=NOW + timedelta(hours=22))
    stale = _incident(db, "CONTENT_DISPATCH_FAILED", hospital=hospital, state="RETRYING",
                      seen=NOW - timedelta(hours=25), sla=NOW - timedelta(hours=1))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, fresh) == "RETRYING"
    assert _state(db, stale) == "ACKNOWLEDGED"


def test_cohort_over_limit_closes_when_its_month_ends(db):
    past = _incident(db, "MONTHLY_SOV_COHORT_OVER_LIMIT", source_id="2026-09")
    current = _incident(db, "MONTHLY_SOV_COHORT_OVER_LIMIT", source_id="2026-10")

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, past) == "ACKNOWLEDGED"
    assert _state(db, current) == "OPEN"


def test_unverified_actor_closes_after_thirty_quiet_days(db):
    quiet = _incident(db, "UNVERIFIED_ADMIN_ACTOR", source_id="unverified:x", seen=NOW - timedelta(days=31))
    recent = _incident(db, "UNVERIFIED_ADMIN_ACTOR", source_id="unverified:y", seen=NOW - timedelta(days=1))

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, quiet) == "ACKNOWLEDGED"
    assert _state(db, recent) == "OPEN"
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_backlog_resolvers_postgres.py -v` Expected: 근거 있는 쪽 단언이 FAIL — `AssertionError: assert 'OPEN' == 'ACKNOWLEDGED'`(RETRYING 두 건은 `'RETRYING' == 'ACKNOWLEDGED'`). 남아 있어야 하는 쪽 단언은 PASS.

- [ ] **Step 3: 최소 구현** — `backend/app/workers/incident_backlog.py`

(a) import:

```python
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased

from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.models.lead_diagnosis import (
    REPORTABLE_EXECUTION_STATUSES,
    DeliveryStatus,
    LeadDelivery,
    LeadDiagnosis,
    LeadReportArtifact,
    ReportStatus,
)
...
from app.services.incident_types import DEFAULT_RETRYING_DEADLINE, SWEEP_OWNED_INCIDENT_TYPES
```

(b) `_lead_diagnosis_gone`(`:225`)에 인자 추가:

```python
def _lead_diagnosis_gone(
    db: Session, incident: Incident, source_types: tuple[str, ...] = ("LEAD_DIAGNOSIS",)
) -> tuple[LeadDiagnosis | None, str | None]:
    ...
    if incident.source_type not in source_types:
        return None, None
```

(c) `_lead_report_recovered`(`:305`)에 키워드 인자 추가하고 전달:

```python
def _lead_report_recovered(
    db: Session, incident: Incident, _now: datetime,
    *, source_types: tuple[str, ...] = ("LEAD_DIAGNOSIS",),
) -> str | None:
    diagnosis, gone = _lead_diagnosis_gone(db, incident, source_types)
```

(d) `_monthly_cohort_enrolled` 뒤에 resolver 추가:

```python
def _uuid_source(incident: Incident) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(incident.source_id))
    except ValueError:
        return None


def _lead_delivery_settled(db: Session, incident: Incident, _now: datetime) -> str | None:
    delivery_id = _uuid_source(incident)
    if delivery_id is None:
        return None
    delivery = db.scalar(
        select(LeadDelivery).where(LeadDelivery.id == delivery_id)
        .execution_options(populate_existing=True)
    )
    if delivery is None:
        return "lead_delivery_missing"  # 리드 파기(CASCADE)로 보낼 대상이 없다.
    if delivery.status == DeliveryStatus.SENT.value:
        return "lead_delivery_sent"
    if delivery.diagnosis_id is None:
        return None
    later_sent = db.scalar(
        select(LeadDelivery.id).where(
            LeadDelivery.diagnosis_id == delivery.diagnosis_id,
            LeadDelivery.status == DeliveryStatus.SENT.value,
            LeadDelivery.created_at > incident.first_seen_at,
        ).limit(1)
    )
    return "later_lead_delivery_sent" if later_sent is not None else None


def _must_use_version_replaced(db: Session, incident: Incident, _now: datetime) -> str | None:
    philosophy_id = _uuid_source(incident)
    if philosophy_id is None:
        return None
    status = db.scalar(
        select(HospitalContentPhilosophy.status).where(HospitalContentPhilosophy.id == philosophy_id)
    )
    if status is None:
        return "approved_version_missing"
    return None if status == PhilosophyStatus.APPROVED else "approved_version_replaced"


def _naver_post_stored(db: Session, incident: Incident, _now: datetime) -> str | None:
    if incident.hospital_id is None or not incident.source_id:
        return None
    items = cast(OperationRun.result_summary, JSONB)["items"]
    url_hash = str(incident.source_id)
    stored = db.scalar(
        select(OperationRun.id).where(
            OperationRun.hospital_id == incident.hospital_id,
            OperationRun.request_payload["source_type"].as_string() == "NAVER_BLOG",
            OperationRun.requested_at > incident.first_seen_at,
            or_(
                items.contains([{"url_hash": url_hash, "state": "INGESTED"}]),
                items.contains([{"url_hash": url_hash, "state": "SKIPPED"}]),
            ),
        ).limit(1)
    )
    return "naver_post_stored" if stored is not None else None


def _broker_accepted_later(db: Session, incident: Incident, _now: datetime) -> str | None:
    failed = db.get(OperationRun, incident.operation_run_id) if incident.operation_run_id else None
    if failed is None:
        return None
    accepted = db.scalar(
        select(OperationRun.id).where(
            OperationRun.operation_type == failed.operation_type,
            OperationRun.hospital_id.is_not_distinct_from(incident.hospital_id),
            OperationRun.queued_at > incident.last_seen_at,
        ).limit(1)
    )
    return "broker_accepted_later_dispatch" if accepted is not None else None


def _site_build_dispatch_superseded(db: Session, incident: Incident, _now: datetime) -> str | None:
    if incident.hospital_id is None:
        return None
    hospital = db.get(Hospital, incident.hospital_id)
    if hospital is None:
        return "hospital_missing"
    if hospital.site_built:
        return "site_built"
    # 1분 자율 복구가 사이트 준비를 맡았다. 그 예산 소진은 SITE_BUILD_RETRIES_EXHAUSTED가 따로 연다.
    swept = db.scalar(
        select(OperationRun.id).where(
            OperationRun.hospital_id == incident.hospital_id,
            OperationRun.operation_type == "REBUILD_SITE",
            OperationRun.requested_at > incident.first_seen_at,
        ).limit(1)
    )
    return "site_build_sweep_owns_retry" if swept is not None else None


def _content_dispatch_superseded(db: Session, incident: Incident, now: datetime) -> str | None:
    if incident.hospital_id is None:
        return None
    first_day = incident.first_seen_at.astimezone(_KST).date()
    unwritten = db.scalar(
        select(ContentItem.id).where(
            ContentItem.hospital_id == incident.hospital_id,
            ContentItem.scheduled_date.between(first_day, first_day + timedelta(days=1)),
            ContentItem.status.in_((ContentStatus.DRAFT, ContentStatus.READY)),
            or_(ContentItem.body.is_(None), func.btrim(ContentItem.body) == ""),
        ).limit(1)
    )
    if unwritten is None:
        return "near_slots_generated"
    # 하루가 지나면 남은 글은 글 단위 생성 사고(CONTENT_GENERATION_FAILED)가 소유한다.
    if now - incident.first_seen_at >= DEFAULT_RETRYING_DEADLINE:
        return "per_item_generation_owns_retry"
    return None


def _month_closed(_db: Session, incident: Incident, now: datetime) -> str | None:
    try:
        first = datetime.strptime(str(incident.source_id or ""), "%Y-%m").date()
    except ValueError:
        return None
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return "cohort_month_ended" if now.astimezone(_KST).date() > last else None


_UNVERIFIED_ACTOR_QUIET_PERIOD = timedelta(days=30)


def _unverified_actor_quiet(_db: Session, incident: Incident, now: datetime) -> str | None:
    if incident.last_seen_at <= now - _UNVERIFIED_ACTOR_QUIET_PERIOD:
        return "no_repeat_for_30_days"
    return None
```

(`date` import는 기존에 쓰인다. 위에서 새로 쓰지 않으면 그대로 둔다.)

(e) `RESOLVERS`(`:333-344`)에 추가:

```python
RESOLVERS: dict[str, Resolver] = {
    "MONTHLY_SOV_COHORT_GAP": _monthly_cohort_enrolled,
    "WEEKLY_SOV_MEASUREMENT_FAILED": _same_period_measurement,
    "MONTHLY_SOV_MEASUREMENT_FAILED": _same_period_measurement,
    "SOV_HIGH_PRIORITY_CAP_EXCEEDED": _weekly_capacity,
    "COST_GUARD_LIMIT_REACHED": _budget_period,
    "V0_REPORT_FAILED": _v0_report_created,
    "CONTENT_GENERATION_FAILED": _content_published,
    "NOTIFICATION_DELIVERY_UNKNOWN": _redirected_delivery,
    "RECOVER_LEAD_MEASUREMENT": _lead_measurement_recovered,
    "RECOVER_LEAD_REPORT": _lead_report_recovered,
    # Phase 1-2: 닫는 코드가 없던 종류
    "LEAD_DIAGNOSIS_FAILED": _lead_measurement_recovered,
    "LEAD_DIAGNOSIS_RETRIES_EXHAUSTED": _lead_measurement_recovered,
    "LEAD_REPORT_BLOCKED": partial(_lead_report_recovered, source_types=("LEAD_REPORT",)),
    "LEAD_DELIVERY_ABANDONED": _lead_delivery_settled,
    "ESSENCE_MUST_USE_EXCLUDED": _must_use_version_replaced,
    "NAVER_SOURCE_FETCH_FAILED": _naver_post_stored,
    "BROKER_UNAVAILABLE": _broker_accepted_later,
    "SITE_BUILD_DISPATCH_FAILED": _site_build_dispatch_superseded,
    "CONTENT_DISPATCH_FAILED": _content_dispatch_superseded,
    "MONTHLY_SOV_COHORT_OVER_LIMIT": _month_closed,
    "UNVERIFIED_ADMIN_ACTOR": _unverified_actor_quiet,
}
# 스윕 소유 종류는 RETRYING으로 열리므로 그 상태에서도 근거로 닫는다. 나머지 RETRYING은 자동화의 것이다.
_RETRYING_RESOLVABLE = tuple(sorted(SWEEP_OWNED_INCIDENT_TYPES & set(RESOLVERS)))
```

(f) `close_resolved_backlog_incidents`(`:347-408`)의 후보 조건과 전이를 바꾼다.

```python
    candidates = list(
        db.execute(
            select(Incident)
            .where(
                or_(
                    Incident.state == IncidentState.OPEN.value,
                    and_(
                        Incident.state == IncidentState.RETRYING.value,
                        Incident.incident_type.in_(_RETRYING_RESOLVABLE),
                    ),
                ),
                Incident.incident_type.in_(tuple(RESOLVERS)),
                or_(
                    Incident.incident_type != "NOTIFICATION_DELIVERY_UNKNOWN",
                    _delivery_unknown_evidence(Incident.source_id, Incident.first_seen_at),
                ),
            )
            ...
    )
    closed = 0
    for incident in candidates:
        if closed >= limit:
            break
        reason = RESOLVERS[incident.incident_type](db, incident, observed)
        if reason is None:
            continue
        retrying = incident
        if incident.state == IncidentState.OPEN.value:
            retrying = _transition_incident(
                db, incident, expected_state=IncidentState.OPEN, next_state=IncidentState.RETRYING
            )
            if retrying is None:
                continue
        recovered = _transition_incident(
            db, retrying, expected_state=IncidentState.RETRYING,
            next_state=IncidentState.RECOVERED, recovered=True,
        )
        ...(이하 종전과 같음)
```

모듈 docstring의 "자동 재시도 중(RETRYING)인 사고는 자동화의 것이라 건드리지 않는다." 뒤에 "단 스윕 소유 종류(`SWEEP_OWNED_INCIDENT_TYPES`)는 처음부터 RETRYING으로 열리므로 같은 근거로 닫는다."를 더한다.

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_backlog_resolvers_postgres.py tests/integration/test_incident_backlog_postgres.py tests/integration/test_lead_recovery_incident_backlog_postgres.py -v` Expected: 모두 PASS(`test_retrying_incidents_belong_to_automation`은 COST_GUARD가 sweep_owned가 아니라 계속 PASS).

- [ ] **Step 5: 커밋** `git add backend/app/workers/incident_backlog.py backend/tests/integration/test_incident_backlog_resolvers_postgres.py && git commit -m "fix(incidents): 영구 OPEN이던 리드·필수문구·네이버·브로커·큐 투입·코호트·actor 사고를 DB 근거로 닫는다"`

**계약 영향:** CLAUDE.md 자율 작업 절에 "닫는 근거는 종류마다 `workers/incident_backlog.RESOLVERS`에 둔다. 근거 없이 영원히 OPEN인 종류를 새로 만들지 않는다(새 종류는 resolver 또는 명시적 복구 경로를 함께 둔다)."
**위험·롤백:** 성급한 닫힘이 위험이다. 각 규칙은 "그 대상"의 사실만 본다(다른 진단·다른 병원·다른 달 성공은 근거 아님 — 음성 테스트로 고정). `CONTENT_DISPATCH_FAILED`의 24시간 규칙만 근거 없는 시간 기반이며, 그 뒤는 글 단위 사고가 소유한다는 구조 근거가 있다. 롤백은 커밋 되돌리기(닫힌 행은 ACKNOWLEDGED·감사 `incident_resolved_by_later_evidence`로 추적 가능).

---

### Task 1-5: 자동 정착 — 연결 실행 성공한 RETRYING과 오래된 RECOVERED를 기계가 닫는다

**Files:**
- Modify: `backend/app/workers/incident_backlog.py` (`settle_incidents`에 두 단계 추가, import)
- Test: `backend/tests/integration/test_incident_settlement_postgres.py` (신규)

**조사 결과:**
- Admin "복구 확인 완료"(`operations_center_serializers.py:252-262`, 서버 `operations_center_incident_routes.py:113-150`)는 RETRYING + 연결 실행 SUCCEEDED + 병원 범위 일치(전역 사고는 `hospital_id IS NULL` 실행)를 요구한다. "문제 확인 완료"(`:265-274`)는 RECOVERED면 언제든 가능하다.
- RECOVERED에서 멈추는 기계 경로: 리드 복구(`lead_recovery_incidents.py:122-136`), 네이버(`naver_handoff_incidents.py:97-120`), 월간 슬롯(`monthly_slot_incident_control.py:109-144`), 주간/월간 측정(`weekly_sov_incident_control.py:299-336`), 주제 교체(`topic_swap_fallback.py:500-531`). RECOVERED는 이미 사람의 일이 아니다(`requires_operator_action`) — 정착은 "확인됨" 화면의 클릭을 없애는 미관 정리다(**감사가 말한 효과보다 작다**).
- **감사와 다른 결정**: Admin 버튼 술어를 그대로 자동화하면 위험하다. 콘텐츠 생성 사고는 연결 실행(`generation_incident_control.py` `current.operation_run_id = run_id`)이 SUCCEEDED여도 글이 여전히 막혀 있을 수 있고 자체 복구가 있다. 그래서 ① 실행 완료 시각이 사고의 마지막 관측보다 뒤이고 ② `source_type`이 `CONTENT_GENERATION`이 아닐 때만 닫는다.
- 짝 계약: 닫을 때 그 사고에 INCIDENT_OPEN이 있었다면 RECOVERED를 넣는다(`record_task_success`와 같은 규칙, `task_incident_control.py:169-184`). 그 OPEN이 나가지 않았다면 1-4가 RECOVERED도 종결한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/integration/test_incident_settlement_postgres.py`

```python
"""자동화가 끝낸 일을 사람이 확인 클릭하지 않게 한다(Phase 1-5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AdminAuditLog
from app.models.operations import Incident, NotificationOutbox, OperationRun
from app.workers.incident_backlog import settle_incidents

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


@pytest.fixture
def db(pg_conn):
    session = Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _incident(db, *, state="RETRYING", source_type="LEAD_DIAGNOSIS", run=None,
              seen=NOW - timedelta(hours=3), recovered_at=None):
    incident = Incident(
        dedupe_key=f"settle-test:{uuid.uuid4()}",
        incident_type="RECOVER_LEAD_MEASUREMENT",
        state=state,
        severity="HIGH",
        customer_impact="테스트",
        source_type=source_type,
        next_action="테스트",
        admin_path="/operations",
        operation_run_id=run.id if run else None,
        sla_due_at=NOW + timedelta(hours=10) if state == "RETRYING" else None,
        first_seen_at=seen,
        last_seen_at=seen,
        recovered_at=recovered_at,
    )
    db.add(incident)
    db.flush()
    return incident


def _run(db, *, completed_at, state="SUCCEEDED"):
    run = OperationRun(
        operation_type="RECOVER_LEAD_MEASUREMENT", state=state, request_payload={},
        requested_at=completed_at - timedelta(minutes=5), completed_at=completed_at,
    )
    db.add(run)
    db.flush()
    return run


def _reload(db, incident) -> Incident:
    db.expire_all()
    return db.get(Incident, incident.id)


def test_retrying_with_a_later_linked_success_is_closed_by_the_machine(db):
    run = _run(db, completed_at=NOW - timedelta(hours=1))
    incident = _incident(db, run=run)

    settle_incidents(db, now=NOW)

    closed = _reload(db, incident)
    assert closed.state == "ACKNOWLEDGED"
    assert closed.acknowledged_by_id is None
    actions = db.scalars(
        select(AdminAuditLog.action).where(AdminAuditLog.target_id == str(incident.id))
    ).all()
    assert "incident_closed_by_linked_success" in actions


def test_a_success_older_than_the_last_failure_is_not_evidence(db):
    run = _run(db, completed_at=NOW - timedelta(hours=5))
    incident = _incident(db, run=run)

    settle_incidents(db, now=NOW)

    assert _reload(db, incident).state == "RETRYING"


def test_content_generation_incidents_are_left_to_their_own_recovery(db):
    run = _run(db, completed_at=NOW - timedelta(hours=1))
    incident = _incident(db, run=run, source_type="CONTENT_GENERATION")

    settle_incidents(db, now=NOW)

    assert _reload(db, incident).state == "RETRYING"


def test_a_delivered_open_is_followed_by_its_recovery_notice(db):
    run = _run(db, completed_at=NOW - timedelta(hours=1))
    incident = _incident(db, run=run)
    db.add(
        NotificationOutbox(
            dedupe_key=f"INCIDENT_OPEN:{incident.id}:e1", notification_type="INCIDENT_OPEN",
            channel="SLACK", state="SENT", incident_id=incident.id, payload={},
            fallback_text="테스트", next_attempt_at=None, sent_at=NOW - timedelta(hours=2),
        )
    )
    db.flush()

    settle_incidents(db, now=NOW)

    kinds = db.scalars(
        select(NotificationOutbox.notification_type).where(NotificationOutbox.incident_id == incident.id)
    ).all()
    assert sorted(kinds) == ["INCIDENT_OPEN", "INCIDENT_RECOVERED"]


def test_recovered_incidents_are_acknowledged_after_a_day(db):
    old = _incident(db, state="RECOVERED", recovered_at=NOW - timedelta(hours=25))
    fresh = _incident(db, state="RECOVERED", recovered_at=NOW - timedelta(hours=1))

    settle_incidents(db, now=NOW)

    assert _reload(db, old).state == "ACKNOWLEDGED"
    assert _reload(db, old).acknowledged_by_id is None
    assert _reload(db, fresh).state == "RECOVERED"
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_incident_settlement_postgres.py -v` Expected: 닫혀야 하는 3건 FAIL — `AssertionError: assert 'RETRYING' == 'ACKNOWLEDGED'` / `'RECOVERED' == 'ACKNOWLEDGED'`; 남아야 하는 건은 PASS.

- [ ] **Step 3: 최소 구현** — `backend/app/workers/incident_backlog.py`

import 추가:

```python
from app.core.config import settings
from app.services.dependency_incident_helpers import open_notice_exists_sync
from app.services.notification_messages import build_recovered_incident_notification
from app.services.notification_store import enqueue_notification_sync
from app.workers.task_incident_control import _audit, _projection, _transition_incident
```

`bound_unowned_retrying` 뒤에 추가하고 `settle_incidents`를 확장한다.

```python
# 생성 사고는 연결 실행이 끝나도 글이 막혀 있을 수 있고 자체 복구(`generation_incident_control`)가 닫는다.
_OWNER_MANAGED_SOURCES = ("CONTENT_GENERATION",)
_RECOVERED_ACK_GRACE = timedelta(hours=24)


def close_retrying_with_linked_success(
    db: Session, *, now: datetime, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """Admin '복구 확인 완료'와 같은 근거(연결 실행 SUCCEEDED·같은 병원 범위)를 기계가 확인해 닫는다.

    버튼보다 좁다: 그 성공이 사고의 마지막 관측 뒤에 끝났을 때만이다.
    """

    rows = db.execute(
        select(Incident, OperationRun)
        .join(OperationRun, OperationRun.id == Incident.operation_run_id)
        .where(
            Incident.state == IncidentState.RETRYING.value,
            Incident.source_type.notin_(_OWNER_MANAGED_SOURCES),
            OperationRun.state == OperationRunState.SUCCEEDED.value,
            OperationRun.hospital_id.is_not_distinct_from(Incident.hospital_id),
            OperationRun.completed_at > Incident.last_seen_at,
        )
        .order_by(Incident.last_seen_at, Incident.id)
        .with_for_update(of=Incident, skip_locked=True)
        .limit(limit)
    ).all()
    closed = 0
    for incident, linked in rows:
        recovered = _transition_incident(
            db, incident, expected_state=IncidentState.RETRYING,
            next_state=IncidentState.RECOVERED, recovered=True,
        )
        if recovered is None:
            continue
        if open_notice_exists_sync(db, recovered.id):
            enqueue_notification_sync(
                db,
                build_recovered_incident_notification(
                    _projection(db, recovered), settings.ADMIN_BASE_URL
                ),
            )
        acknowledged = _transition_incident(
            db, recovered, expected_state=IncidentState.RECOVERED,
            next_state=IncidentState.ACKNOWLEDGED, acknowledged=True,
        )
        _audit(
            db,
            acknowledged or recovered,
            "incident_closed_by_linked_success",
            detail_extra={"evidence": "linked_run_succeeded", "linked_run_id": str(linked.id)},
        )
        closed += 1
    return closed


def acknowledge_settled_recoveries(
    db: Session, *, now: datetime, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """복구된 지 하루가 지난 사고를 시스템 확인으로 닫는다(acknowledged_by_id NULL = 기계가 닫음)."""

    rows = list(
        db.execute(
            select(Incident)
            .where(
                Incident.state == IncidentState.RECOVERED.value,
                Incident.recovered_at <= now - _RECOVERED_ACK_GRACE,
            )
            .order_by(Incident.recovered_at, Incident.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    closed = 0
    for incident in rows:
        acknowledged = _transition_incident(
            db, incident, expected_state=IncidentState.RECOVERED,
            next_state=IncidentState.ACKNOWLEDGED, acknowledged=True,
        )
        if acknowledged is None:
            continue
        _audit(db, acknowledged, "incident_auto_acknowledged",
               detail_extra={"evidence": "recovered_over_24h"})
        closed += 1
    return closed


def settle_incidents(
    db: Session, *, now: datetime | None = None, limit: int = BACKLOG_INCIDENT_BATCH
) -> int:
    """자동화가 끝낸 일을 사람의 일로 남기지 않는 정리. Slack은 짝 계약이 요구할 때만. 커밋은 호출자."""

    observed = now or datetime.now(UTC)
    return (
        bound_unowned_retrying(db, now=observed, limit=limit)
        + close_retrying_with_linked_success(db, now=observed, limit=limit)
        + acknowledge_settled_recoveries(db, now=observed, limit=limit)
    )
```

순환 import 확인: `notification_store`는 `incidents`·`incident_types`만 import하고 `incident_backlog`를 import하지 않는다. `python -c "import app.workers.autonomous_recovery"`로 확인.

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -c "import app.workers.autonomous_recovery" && .venv/bin/python -m pytest tests/integration/test_incident_settlement_postgres.py tests/integration/test_incident_retry_deadline_postgres.py tests/test_autonomous_loop_recovery.py -v` Expected: import 오류 없음, 모두 PASS.

- [ ] **Step 5: 커밋** `git add backend/app/workers/incident_backlog.py backend/tests/integration/test_incident_settlement_postgres.py && git commit -m "feat(incidents): 연결 실행이 성공한 RETRYING과 하루 지난 RECOVERED를 시스템이 닫는다"`

**계약 영향:** CLAUDE.md "사람의 일 건수는…" 절에 "복구 확인·문제 확인은 사람의 일이 아니다 — 연결 실행이 마지막 실패 뒤 성공하면(생성 사고 제외) 매 분 정착이 닫고, 복구된 지 24시간이 지난 사고는 시스템 확인으로 닫는다."
**위험·롤백:** 연결 실행 SUCCEEDED인데 실제로는 일이 남은 종류가 있으면 잘못 닫는다 — 재발 시 `open_or_touch_incident`가 새 에피소드로 다시 연다(Slack도 새로). 생성 사고는 제외. 롤백은 커밋 되돌리기.

---

### Task 1-8: 대상 재분류와 사고→상태 강등

**Files:**
- Modify: `backend/app/services/incident_types.py` (`INCIDENT_TYPE_POLICIES`)
- Test: `backend/tests/test_incident_type_policy.py` (추가), `backend/tests/test_notification_outbox.py:142-153` (파라미터 목록에 추가)

**조사 결과 (종류별 여는 곳 → 대신 보여 줄 화면/상태):**

| 종류 | 여는 곳 | 결정 | 근거 / 화면 |
|---|---|---|---|
| `UNVERIFIED_ADMIN_ACTOR` | `core/security.py:271-296` | 개발 | 위조/불일치 actor 헤더 — BFF 서명 문제. AE가 고칠 수 없음. 30일 무재발이면 1-2가 닫음. 운영센터 "개발 확인"으로만 셈 |
| `MONTHLY_SOV_COHORT_OVER_LIMIT` | `workers/monthly_cohort_incidents.py:66-102` | 개발 | 조치가 env(`SOV_MONTHLY_COHORT_LIMIT`·`COST_GUARD_MONTHLY_SOV_QUERIES`) 변경 — 배포 일. 원인 수정(한도 계산)은 1-6 몫 |
| `COST_GUARD_LIMIT_REACHED` | `services/cost_guard.py:888-916` | 개발(**대표 확인 필요**) | 다음 조치가 "킬스위치·상한 조정"(`:902-905`)인데 이는 OWNER 결정이다. AE 큐에서는 빼되, 비용 알림 자체(`_build_cost_alert_intent`)는 사고 알림 경로가 아니라 채널 판정이 바뀌지 않는다 |
| `SITE_BUILD_RETRIES_EXHAUSTED` | `autonomous_recovery.py:564-607`(`site_build_incidents.open_site_build_incident`, `incident_type=safe_error_code`) | 개발 | 하루 3회 자동 재실행 소진 = 코드·인프라 문제. 사람이 누른 재시도 실패(`SITE_BUILD_FAILED`, `task_incident_control.py:202-249`)는 운영 그대로 |
| `SOV_HIGH_PRIORITY_CAP_EXCEEDED` | `workers/weekly_sov_incident_control.py:61-110`(이미 Slack 없음) | 조용함 | 주간 측정 상한 다이제스트. 행·`source_id=week`는 남고 주 종료 시 1-2 기존 resolver가 닫음. 운영센터 상태 필터(`status=OPEN`, 전체)로 조회 |
| `WEEKLY_SOV_MEASUREMENT_FAILED` | `weekly_sov_incident_control.py:187-280` | 조용함 | 주간 측정은 월간 근거의 보조다. 월간 측정 실패(`MONTHLY_SOV_MEASUREMENT_FAILED`)·코호트 공백(`MONTHLY_SOV_COHORT_GAP`)이 사람의 일을 따로 연다. 주의: 같은 사고에 묶인 정책 전환 안내(`SOV_MEASUREMENT_POLICY_TRANSITION`, "조치 필요 없음")도 조용함 필터(`notification_store.py:87-91`)로 함께 빠진다 |
| `NOTIFICATION_DELIVERY_UNKNOWN` | `notification_store.py:252-275` | 개발+조용함 | 채널 단위 전송 사고(`NOTIFICATION_DELIVERY_FAILED`)가 정본(CLAUDE.md). 행 단위 불명은 1-2 기존 resolver가 정리 |
| `SOURCE_PROCESSING_FAILED` | `tasks.py:3810-3835` | **강등하지 않음(감사와 다름)** | `tasks.py:3822-3824`(I-3): ERROR는 자동 재시도 없는 종착이고 사람이 자료를 고치거나 빼야 한다. 이 사고가 유일한 신호다. 채널 URL fetch 실패처럼 "필수 자료가 없을 때만 상태 카드"로 바꾸려면 상태 카드 확장이 먼저다 — 별도 과제 |

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_incident_type_policy.py`에 추가

```python
from datetime import datetime as _dt

from app.services.operator_action import is_operator_todo


@pytest.mark.parametrize(
    "incident_type",
    [
        "UNVERIFIED_ADMIN_ACTOR",
        "MONTHLY_SOV_COHORT_OVER_LIMIT",
        "COST_GUARD_LIMIT_REACHED",
        "SITE_BUILD_RETRIES_EXHAUSTED",
    ],
)
def test_work_an_ae_cannot_do_goes_to_the_developer(incident_type):
    assert incident_audience(incident_type) is IncidentAudience.DEVELOPER
    assert is_operator_todo(incident_type, "OPEN", None, _dt(2026, 10, 26, tzinfo=UTC)) is False


@pytest.mark.parametrize(
    "incident_type",
    ["SOV_HIGH_PRIORITY_CAP_EXCEEDED", "WEEKLY_SOV_MEASUREMENT_FAILED", "NOTIFICATION_DELIVERY_UNKNOWN"],
)
def test_state_only_kinds_are_quiet(incident_type):
    assert incident_is_quiet(incident_type) is True


def test_people_owned_kinds_stay_with_the_operator():
    for incident_type in ("SITE_BUILD_FAILED", "SOURCE_PROCESSING_FAILED", "MONTHLY_SOV_COHORT_GAP"):
        assert incident_audience(incident_type) is IncidentAudience.OPERATOR
        assert incident_is_quiet(incident_type) is False
```

`tests/test_notification_outbox.py:142-153` 개발 대상 파라미터 목록에 `"UNVERIFIED_ADMIN_ACTOR"`, `"MONTHLY_SOV_COHORT_OVER_LIMIT"`, `"COST_GUARD_LIMIT_REACHED"`, `"SITE_BUILD_RETRIES_EXHAUSTED"`를 더한다.

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_incident_type_policy.py tests/test_notification_outbox.py -v` Expected: 새 파라미터 FAIL — `AssertionError: assert <IncidentAudience.OPERATOR: 'operator'> is <IncidentAudience.DEVELOPER: 'developer'>` / `assert False is True`.

- [ ] **Step 3: 최소 구현** — `INCIDENT_TYPE_POLICIES`에 추가하고 `NOTIFICATION_DELIVERY_UNKNOWN` 항목을 바꾼다.

```python
    "NOTIFICATION_DELIVERY_UNKNOWN": IncidentTypePolicy(
        audience=IncidentAudience.DEVELOPER, quiet=True
    ),
    ...
    # Phase 1-8: AE가 Admin에서 고칠 수 없는 일 → 개발 담당.
    "UNVERIFIED_ADMIN_ACTOR": _DEVELOPER,
    "MONTHLY_SOV_COHORT_OVER_LIMIT": _DEVELOPER,
    "COST_GUARD_LIMIT_REACHED": _DEVELOPER,
    "SITE_BUILD_RETRIES_EXHAUSTED": _DEVELOPER,
    # Phase 1-8: 상태로 충분한 관찰 — 행은 남고 Slack·할 일·요약 건수에서 빠진다.
    "SOV_HIGH_PRIORITY_CAP_EXCEEDED": IncidentTypePolicy(quiet=True),
    "WEEKLY_SOV_MEASUREMENT_FAILED": IncidentTypePolicy(quiet=True),
```

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_incident_type_policy.py tests/test_notification_outbox.py tests/test_notification_labels.py tests/test_cost_guard.py tests/test_operations_actionability.py tests/test_domain_health_public_site_grouping.py -v` Expected: 모두 PASS. 실패하면 그 테스트가 옛 대상을 단언하고 있는 것이므로 새 분류로 기대값을 고친다(예: 운영 채널 기대).

- [ ] **Step 5: 커밋** `git add backend/app/services/incident_types.py backend/tests/test_incident_type_policy.py backend/tests/test_notification_outbox.py && git commit -m "fix(incidents): AE가 못 고치는 사고는 개발 담당으로, 관찰용 사고는 조용한 상태로 재분류한다"`

**계약 영향:** CLAUDE.md "Slack 채널은 운영 채널 하나다… `IncidentAudience`(운영/개발 대상)는 유지하되" 문단에 "대상·조용함은 `incident_types.INCIDENT_TYPE_POLICIES` 한 표에서 정한다. 주간 측정 실패·주간 상한 초과·행 단위 전송 불명은 조용한 상태 기록이다." 추가. "외부 감시… 발행 누락은 운영 대상" 문장은 그대로.
**위험·롤백:** 개발 대상은 일일 요약 "개발 확인 M건"으로만 보인다 — 개발 쪽이 그 줄을 실제로 본다는 운영 약속이 필요(감사 위험 "개발 채널 모니터링 필요"). 롤백은 커밋 되돌리기.

---

### Task 1-10a: 사후 검수가 못 푼 HARD 지적은 사람의 일 (비조용 사고)

**Files:**
- Modify: `backend/app/workers/post_publish_ai_review.py:86-92` (상수), `:255-313` (`_NEEDS_HUMAN_COPY`, `_open_flag_incident`)
- Modify: `backend/app/workers/incident_backlog.py` (`POST_PUBLISH_NEEDS_HUMAN` resolver)
- Modify: `backend/app/services/notification_copy.py:66` 부근 (카탈로그 한 항목)
- Test: `backend/tests/test_post_publish_needs_human.py` (신규), `backend/tests/integration/test_incident_backlog_resolvers_postgres.py` (추가)

**조사 결과:**
- 교정 실패(NEEDS_HUMAN·BLOCKED·REJECTED)는 `_open_flag_incident(..., needs_human_reason=...)`로 **같은 키의 조용한** `POST_PUBLISH_REVIEW_FLAGGED`를 갱신만 한다(`post_publish_ai_review.py:264-313`, notify=False). 표시는 `correction.finished=true`로 남아 `_flagged_stmt`(`:134-164`)가 다시 고르지 않는다 → HARD 의료 안전 지적이 남은 글이 기한 없이 공개된다(감사 F10 확인).
- `open_or_touch_incident`의 충돌 갱신은 `incident_type`을 바꾸지 않는다(`incidents.py:102-145`). 같은 키로 종류만 바꿀 수 없으므로 **별도 키**(object_type `content_item_needs_human`)의 새 종류 `POST_PUBLISH_NEEDS_HUMAN`을 연다. 조용한 FLAGGED는 콘텐츠 탭 표시용으로 그대로 둔다.
- 닫는 경로가 여럿이다(교정 성공 `_recover_flag_incident`, 사람의 확인 `content.py:1512-1531`, PATCH로 표시 삭제, 비공개 전환). 경로마다 닫기를 더하는 대신 **글의 현재 상태를 근거로** 1-2 resolver가 1분마다 닫는다.
- **대표 결정과 충돌**: 2026-10-08 대표 지시로 사후검수 지적은 Slack에서 뺐다(`incident_types.py:73-76`). 이 작업은 "HARD가 남은 + 자동 교정이 끝난" 글만 다시 사람의 일·Slack으로 올린다. 하루 20편 표본 중 교정 실패 HARD만이라 양은 작지만 대표 확인이 필요하다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_post_publish_needs_human.py`

```python
"""자동 교정이 못 푼 HARD 지적이 남은 공개 글은 사람의 일로 올린다(Phase 1-10)."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services import ops_incident_alerts
from app.workers import post_publish_ai_review as sweep


def _item(severities):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="가상 공개 글",
        essence_check_summary={
            "post_publish_ai_review": {
                "status": "FLAGGED",
                "findings": ["지적"],
                "structured_findings": [
                    {"severity": s, "kind": "MEDICAL_SAFETY", "quote": "q", "message": "지적"}
                    for s in severities
                ],
            }
        },
    )


def _capture(monkeypatch):
    opened: list[dict] = []

    async def fake_open(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(ops_incident_alerts, "open_ops_incident", fake_open)
    return opened


HOSPITAL = SimpleNamespace(id=uuid.uuid4(), name="가상의원")


def test_hard_finding_left_after_correction_opens_a_people_incident(monkeypatch):
    opened = _capture(monkeypatch)

    sweep._open_flag_incident(_item(["HARD"]), HOSPITAL, asyncio.run, needs_human_reason="blocked")

    kinds = [call["incident_type"] for call in opened]
    assert kinds == ["POST_PUBLISH_REVIEW_FLAGGED", "POST_PUBLISH_NEEDS_HUMAN"]
    human = opened[1]
    assert human["notify"] is True
    assert human["object_type"] == "content_item_needs_human"


def test_uncertain_only_stays_quiet_for_the_senior_rereview(monkeypatch):
    opened = _capture(monkeypatch)

    sweep._open_flag_incident(_item(["UNCERTAIN"]), HOSPITAL, asyncio.run, needs_human_reason="TITLE_FINDING")

    assert [call["incident_type"] for call in opened] == ["POST_PUBLISH_REVIEW_FLAGGED"]


def test_first_flag_without_a_correction_attempt_is_not_people_work_yet(monkeypatch):
    opened = _capture(monkeypatch)

    sweep._open_flag_incident(_item(["HARD"]), HOSPITAL, asyncio.run)

    assert [call["incident_type"] for call in opened] == ["POST_PUBLISH_REVIEW_FLAGGED"]


def test_unknown_structure_is_treated_as_people_work(monkeypatch):
    opened = _capture(monkeypatch)
    item = _item([])
    item.essence_check_summary["post_publish_ai_review"]["structured_findings"] = []

    sweep._open_flag_incident(item, HOSPITAL, asyncio.run, needs_human_reason="NOT_CORRECTABLE")

    assert opened[-1]["incident_type"] == "POST_PUBLISH_NEEDS_HUMAN"
```

`tests/integration/test_incident_backlog_resolvers_postgres.py`에 추가:

```python
def test_needs_human_closes_once_the_flag_is_gone_or_the_post_is_private(db):
    hospital = _hospital(db)
    schedule = ContentSchedule(hospital_id=hospital.id, plan="PLAN_12", publish_days=[1], active_from=date(2026, 9, 1))
    db.add(schedule)
    db.flush()

    def _post(status, summary):
        item = ContentItem(
            hospital_id=hospital.id, schedule_id=schedule.id, content_type=ContentType.DISEASE,
            sequence_no=1, total_count=12, scheduled_date=date(2026, 9, 20), title="가상 글",
            status=status, essence_check_summary=summary,
        )
        db.add(item)
        db.flush()
        return item

    flagged = {"post_publish_ai_review": {"status": "FLAGGED"}}
    still = _post(ContentStatus.PUBLISHED, flagged)
    cleared = _post(ContentStatus.PUBLISHED, {})
    hidden = _post(ContentStatus.WITHHELD, flagged)
    incidents = {
        name: _incident(db, "POST_PUBLISH_NEEDS_HUMAN", hospital=hospital, source_id=str(post.id))
        for name, post in (("still", still), ("cleared", cleared), ("hidden", hidden))
    }

    close_resolved_backlog_incidents(db, now=NOW)

    assert _state(db, incidents["still"]) == "OPEN"
    assert _state(db, incidents["cleared"]) == "ACKNOWLEDGED"
    assert _state(db, incidents["hidden"]) == "ACKNOWLEDGED"
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_post_publish_needs_human.py "tests/integration/test_incident_backlog_resolvers_postgres.py::test_needs_human_closes_once_the_flag_is_gone_or_the_post_is_private" -v` Expected: 단위 2건 FAIL — `assert ['POST_PUBLISH_REVIEW_FLAGGED'] == ['POST_PUBLISH_REVIEW_FLAGGED', 'POST_PUBLISH_NEEDS_HUMAN']`; 통합 FAIL — `'OPEN' == 'ACKNOWLEDGED'`.

- [ ] **Step 3: 최소 구현**

(a) `post_publish_ai_review.py` 상수(`:90` 아래):

```python
POST_PUBLISH_NEEDS_HUMAN_TYPE = "POST_PUBLISH_NEEDS_HUMAN"
POST_PUBLISH_NEEDS_HUMAN_OBJECT = "content_item_needs_human"
```

(b) `_NEEDS_HUMAN_COPY`에 `"SENIOR_REREVIEW_HARD": "상위 검수 모델이 안전 지적을 다시 확인했습니다.",` 추가. 그리고 함수 추가:

```python
def needs_human_safety(marker: object) -> bool:
    """자동 교정이 끝난 뒤에도 사람이 지금 봐야 하는 지적인가 — HARD가 하나라도 있거나 구조를 모른다.

    UNCERTAIN만 남은 글은 사람의 일이 아니라 상위 모델 재검수 대상이다(`senior_rereview_flagged`).
    """

    findings = marker.get("structured_findings") if isinstance(marker, dict) else None
    if not findings:
        return True
    return any(
        isinstance(finding, dict) and str(finding.get("severity", "")).upper() == "HARD"
        for finding in findings
    )
```

(c) `_open_flag_incident` 끝(기존 `run_async(open_ops_incident(...))` 다음)에 추가:

```python
    marker = (item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY)
    if needs_human_reason and needs_human_safety(marker):
        # 조용한 표시(콘텐츠 탭)와 별개로, 안전 지적이 남은 공개 글은 운영 담당의 할 일이다.
        run_async(
            open_ops_incident(
                pipeline=POST_PUBLISH_INCIDENT_PIPELINE,
                object_type=POST_PUBLISH_NEEDS_HUMAN_OBJECT,
                object_id=str(item.id),
                incident_type=POST_PUBLISH_NEEDS_HUMAN_TYPE,
                safe_error_code=POST_PUBLISH_NEEDS_HUMAN_TYPE,
                problem=problem,
                customer_impact="안전 지적이 남은 글이 공개되어 있습니다. 자동으로 내리지 않았습니다.",
                next_action=next_action,
                source_type="POST_PUBLISH_AI_REVIEW",
                hospital_name=hospital.name,
                hospital_id=hospital.id,
                admin_path=f"/hospitals/{hospital.id}/content",
                fingerprint=IncidentFingerprint.SAFETY_BLOCKED,
                actor=POST_PUBLISH_AI_REVIEWER,
                notify=True,
            )
        )
```

(d) `notification_copy.py` `_CATALOG`에 추가:

```python
    "POST_PUBLISH_NEEDS_HUMAN": ActionCopy(
        "공개 글 안전 지적 확인 필요",
        "콘텐츠에서 해당 글의 지적 문장을 직접 고치거나 비공개로 돌려 주세요. 문제가 없다고 판단하면 공개 내용 확인을 기록해 주세요.",
        "공개 글 확인",
    ),
```

(e) `incident_backlog.py`에 resolver와 등록:

```python
def _published_flag_cleared(db: Session, incident: Incident, _now: datetime) -> str | None:
    """그 글이 내려갔거나, 사람이 확인했거나, 고쳐져 표시가 사라지면 닫는다."""

    item_id = _uuid_source(incident)
    if item_id is None:
        return None
    item = db.scalar(
        select(ContentItem).where(ContentItem.id == item_id).execution_options(populate_existing=True)
    )
    if item is None:
        return "content_missing"
    if item.status != ContentStatus.PUBLISHED:
        return "content_not_public"
    if item.post_publish_reviewed_at is not None:
        return "post_publish_review_recorded"
    # 키 이름은 `services/published_correction.POST_PUBLISH_FLAG_KEY`와 같다(무거운 import 회피).
    marker = (item.essence_check_summary or {}).get("post_publish_ai_review")
    if not isinstance(marker, dict) or marker.get("status") != "FLAGGED":
        return "post_publish_flag_cleared"
    return None
```

`RESOLVERS`에 `"POST_PUBLISH_NEEDS_HUMAN": _published_flag_cleared,` 추가.

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_post_publish_needs_human.py tests/test_published_auto_correction.py tests/test_post_publish_ai_review.py tests/test_post_publish_flag_manual_review.py tests/integration/test_incident_backlog_resolvers_postgres.py -v` Expected: 모두 PASS.

- [ ] **Step 5: 커밋** `git add backend/app/workers/post_publish_ai_review.py backend/app/workers/incident_backlog.py backend/app/services/notification_copy.py backend/tests/test_post_publish_needs_human.py backend/tests/integration/test_incident_backlog_resolvers_postgres.py && git commit -m "fix(post-publish): 자동 교정이 못 푼 HARD 지적이 남은 공개 글을 사람의 일로 올린다"`

**계약 영향:** CLAUDE.md "후행 검수는 조건부 표본 확인이다… 운영자 큐 행이나 목록 라벨로도 올리지 않으며 콘텐츠 탭과 보고서 증빙에만 보인다." → "…올리지 않는다. 단 자동 교정이 끝나고도 HARD 지적이 남은(또는 지적 구조를 모르는) 공개 글은 `POST_PUBLISH_NEEDS_HUMAN` 사고로 운영 담당의 할 일이며, 글이 고쳐지거나 내려가거나 사람이 확인하면 닫힌다." 그리고 사후검수 절 끝의 "인시던트가 사람이 직접 고쳐야 한다고 말한다" 뒤에 같은 취지.
**위험·롤백:** 알림 소폭 증가(안전 우선, 감사 예상). 롤백은 커밋 되돌리기(열린 NEEDS_HUMAN 행은 resolver가 표시 소거 시 닫는다).

---

### Task 1-10b: UNCERTAIN만 남은 FLAGGED는 N일 뒤 상위 모델로 재검수해 PASS면 닫는다

**Files:**
- Modify: `backend/app/core/config.py:569-570` 부근 (설정 1개), `:410-416` (음수 검증에 포함)
- Modify: `backend/app/workers/post_publish_ai_review.py` (`_uncertain_settled_stmt`, `uncertain_only`, `settled_long_enough`, `senior_rereview_flagged`, 스윕 1b 단계 `:690-715`)
- Test: `backend/tests/test_post_publish_senior_rereview.py` (신규)

**조사 결과:**
- 상위 모델 = `review_generated_content(model=None)` → `settings.CLAUDE_MODEL_REVIEW`(`content_ai_review.py:1009-1014`). 첫 판정은 `POST_PUBLISH_AI_REVIEW_MODEL`(Sonnet).
- `apply_review_outcome`(`post_publish_ai_review.py:208-252`)은 같은 본문 hash면 표시의 `correction` 상태를 보존한다(`_flag_payload` `:198-202`) → 교정 끝남 표시가 유지돼 `_flagged_stmt`가 다시 고르지 않는다. 재검수 1회 표시(`senior_rereview`)를 같은 커밋에 남겨 매일 다시 사지 않게 한다.
- `checked_at`은 표시 안의 ISO 문자열(`:189`).
- 비용 상한: 1단계 교정 상한(`POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP`, `corrections` 카운터)을 함께 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_post_publish_senior_rereview.py`

```python
"""교정으로 못 푼 UNCERTAIN만 남은 공개 글은 상위 모델이 한 번 더 본다(Phase 1-10)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.services.content_ai_review import (
    ContentAiFinding,
    ContentAiFindingKind,
    ContentAiFindingSeverity,
    ContentAiReviewStatus,
    candidate_sha256,
)
from app.workers import post_publish_ai_review as sweep
from tests.test_post_publish_ai_review import _published, _review

NOW = datetime(2026, 10, 20, 3, 10, tzinfo=timezone.utc)


def _flagged(severity="UNCERTAIN", days_ago=8):
    item = _published()
    item.essence_check_summary = {
        "post_publish_ai_review": {
            "status": "FLAGGED",
            "candidate_sha256": candidate_sha256(item),
            "checked_at": (NOW - timedelta(days=days_ago)).isoformat(),
            "findings": ["지적"],
            "structured_findings": [
                {"severity": severity, "kind": "MEDICAL_SAFETY", "quote": "q", "message": "지적"}
            ],
            "correction": {"finished": True, "passes": 2},
        }
    }
    return item


class _DB:
    def rollback(self):
        pass

    def commit(self):
        pass


def _wire(monkeypatch, item, review):
    calls = {"models": [], "recovered": 0, "needs_human": []}

    async def fake_review(**kwargs):
        calls["models"].append(kwargs.get("model"))
        return review

    monkeypatch.setattr(sweep, "review_generated_content", fake_review)
    monkeypatch.setattr(sweep, "_candidate_content", lambda _item: {})
    monkeypatch.setattr(sweep, "_lock_item", lambda _db, _id: item)
    monkeypatch.setattr(
        sweep, "_recover_flag_incident",
        lambda *_a, **_k: calls.__setitem__("recovered", calls["recovered"] + 1),
    )
    monkeypatch.setattr(
        sweep, "_open_flag_incident",
        lambda _item, _h, _r, **k: calls["needs_human"].append(k.get("needs_human_reason")),
    )
    return calls


def test_uncertain_only_and_age_gate():
    uncertain = _flagged().essence_check_summary["post_publish_ai_review"]
    hard = _flagged("HARD").essence_check_summary["post_publish_ai_review"]
    young = _flagged(days_ago=3).essence_check_summary["post_publish_ai_review"]
    assert sweep.uncertain_only(uncertain) is True
    assert sweep.uncertain_only(hard) is False
    assert sweep.settled_long_enough(young, NOW, 7) is False
    assert sweep.settled_long_enough(uncertain, NOW, 7) is True


def test_senior_pass_closes_the_flag_with_the_review_model(monkeypatch):
    item = _flagged()
    calls = _wire(monkeypatch, item, _review(item))

    result = sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    assert result == "REVIEWED"
    assert calls["models"] == [None]  # None = settings.CLAUDE_MODEL_REVIEW
    assert calls["recovered"] == 1
    assert "post_publish_ai_review" not in item.essence_check_summary


def test_senior_still_uncertain_is_recorded_once_and_stays_quiet(monkeypatch):
    item = _flagged()
    uncertain = ContentAiFinding(
        ContentAiFindingSeverity.UNCERTAIN, ContentAiFindingKind.MEDICAL_SAFETY, "확신 부족"
    )
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[uncertain]))

    result = sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    marker = item.essence_check_summary["post_publish_ai_review"]
    assert result == "FLAGGED"
    assert marker["senior_rereview"]["model"] == settings.CLAUDE_MODEL_REVIEW
    assert marker["correction"]["finished"] is True
    assert calls["needs_human"] == []


def test_senior_hard_finding_becomes_people_work(monkeypatch):
    item = _flagged()
    hard = ContentAiFinding(ContentAiFindingSeverity.HARD, ContentAiFindingKind.MEDICAL_SAFETY, "근거 없는 단정")
    calls = _wire(monkeypatch, item, _review(item, status=ContentAiReviewStatus.REVISE, findings=[hard]))

    sweep.senior_rereview_flagged(_DB(), item, object(), object(), asyncio.run, NOW)

    assert calls["needs_human"] == ["SENIOR_REREVIEW_HARD"]
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_post_publish_senior_rereview.py -v` Expected: FAIL — `AttributeError: module 'app.workers.post_publish_ai_review' has no attribute 'uncertain_only'`.

- [ ] **Step 3: 최소 구현**

(a) `config.py` `POST_PUBLISH_AUTO_CORRECTION_MAX_PASSES` 아래:

```python
    # 자동 교정이 끝나고도 UNCERTAIN(확신 부족)만 남은 공개 글을 검수 모델로 한 번 더 보는 대기 일수.
    # PASS면 확인 완료로 닫는다. 0이면 끈다. 비용은 교정 하루 상한(POST_PUBLISH_AUTO_CORRECTION_DAILY_CAP)에 함께 센다.
    POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS: int = 7
```

`:410` 음수 검증 데코레이터 인자 목록에 `"POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS"`를 더한다.

(b) `post_publish_ai_review.py` — import에 `from datetime import UTC, datetime, timedelta`; 상수 `POST_PUBLISH_SENIOR_REREVIEW_KEY = "senior_rereview"`; `_withheld_stmt` 뒤에:

```python
def _uncertain_settled_stmt(limit: int):
    """교정이 끝났는데 FLAGGED로 남고 아직 상위 재검수를 받지 않은 공개 글."""

    flag = ContentItem.essence_check_summary[POST_PUBLISH_FLAG_KEY]
    return (
        select(ContentItem)
        .join(Hospital, ContentItem.hospital_id == Hospital.id)
        .where(
            ContentItem.status == ContentStatus.PUBLISHED,
            ContentItem.post_publish_reviewed_at.is_(None),
            ContentItem.body.is_not(None),
            ContentItem.pending_revision.is_(None),
            publicly_operational_hospital_predicate(),
            flag["status"].as_string() == POST_PUBLISH_FLAGGED,
            flag["correction"]["finished"].as_string() == "true",
            flag[POST_PUBLISH_SENIOR_REREVIEW_KEY].as_string().is_(None),
        )
        .order_by(ContentItem.published_at, ContentItem.id)
        .options(joinedload(ContentItem.hospital))
        .limit(limit)
    )


def uncertain_only(marker: object) -> bool:
    findings = marker.get("structured_findings") if isinstance(marker, dict) else None
    if not findings:
        return False
    return all(
        isinstance(finding, dict) and str(finding.get("severity", "")).upper() == "UNCERTAIN"
        for finding in findings
    )


def settled_long_enough(marker: object, now: datetime, days: int) -> bool:
    try:
        checked = datetime.fromisoformat(str((marker or {}).get("checked_at")))
    except (TypeError, ValueError, AttributeError):
        return False
    return now - checked >= timedelta(days=days)


def senior_rereview_flagged(db, item, hospital, philosophy, run_async, now: datetime) -> str:
    """UNCERTAIN만 남은 공개 글을 검수 모델로 한 번 더 본다. PASS면 확인 완료로 닫고 사고를 닫는다."""

    item_id = item.id
    try:
        review = run_async(
            review_generated_content(
                hospital=hospital,
                philosophy=philosophy,
                content=_candidate_content(item),
                content_brief=getattr(item, "content_brief", None),
                model=None,  # 검수 모델(CLAUDE_MODEL_REVIEW)
            )
        )
    except Exception as error:  # noqa: BLE001 - 한 글의 실패가 하루 스윕을 멈추지 않는다.
        logger.warning("senior post-publish re-review failed for %s: %s", item_id, type(error).__name__)
        db.rollback()
        return "UNAVAILABLE"
    if review.status == ContentAiReviewStatus.UNAVAILABLE:
        db.rollback()
        return "UNAVAILABLE"
    locked = _lock_item(db, item_id)
    if locked is None:
        db.rollback()
        return "SKIPPED"
    outcome = apply_review_outcome(locked, review, now=now)
    if outcome == "FLAGGED":
        summary = dict(locked.essence_check_summary)
        marker = dict(summary[POST_PUBLISH_FLAG_KEY])
        marker[POST_PUBLISH_SENIOR_REREVIEW_KEY] = {
            "at": now.isoformat(),
            "model": settings.CLAUDE_MODEL_REVIEW,
        }
        summary[POST_PUBLISH_FLAG_KEY] = marker
        locked.essence_check_summary = summary
    db.commit()
    if outcome == "REVIEWED":
        _recover_flag_incident(locked, hospital, run_async)
    elif outcome == "FLAGGED" and not uncertain_only(
        locked.essence_check_summary[POST_PUBLISH_FLAG_KEY]
    ):
        _open_flag_incident(locked, hospital, run_async, needs_human_reason="SENIOR_REREVIEW_HARD")
    return outcome
```

(c) `review_post_publish_samples`의 1단계 루프(`:690-715`) 바로 뒤, `if cap <= 0:` 앞에:

```python
        # 1b) 교정으로 못 푼 UNCERTAIN만 남은 글 — 며칠 뒤 검수 모델로 한 번 더 본다(PASS면 닫힌다).
        rereview_days = int(settings.POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS)
        if correction_cap > 0 and rereview_days > 0:
            rereview_now = datetime.now(UTC)
            settled = list(
                db.execute(_uncertain_settled_stmt(correction_cap * _FLAG_SCAN_MULTIPLIER))
                .unique()
                .scalars()
                .all()
            )
            for item in settled:
                if corrections >= correction_cap or out_of_budget():
                    break
                marker = (item.essence_check_summary or {}).get(POST_PUBLISH_FLAG_KEY)
                if not uncertain_only(marker) or not settled_long_enough(
                    marker, rereview_now, rereview_days
                ):
                    continue
                hospital = item.hospital
                if hospital.id not in philosophies:
                    philosophies[hospital.id] = _generation_philosophy_sync(db, hospital.id)
                if philosophies[hospital.id] is None:
                    counts["skipped"] += 1
                    continue
                result = senior_rereview_flagged(
                    db, item, hospital, philosophies[hospital.id], _run_async, rereview_now
                )
                corrections += 1
                tally(result)
                _bump(counts, f"senior_rereview_{result.lower()}")
```

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_post_publish_senior_rereview.py tests/test_post_publish_ai_review.py tests/test_published_auto_correction.py -v` Expected: 모두 PASS.

- [ ] **Step 5: 커밋** `git add backend/app/core/config.py backend/app/workers/post_publish_ai_review.py backend/tests/test_post_publish_senior_rereview.py && git commit -m "feat(post-publish): 교정으로 못 푼 UNCERTAIN 지적은 7일 뒤 검수 모델로 재검수해 PASS면 닫는다"`

**계약 영향:** CLAUDE.md 후행 검수 절 "확신도 부족으로 애매하면 검수 모델(Opus)이 한 번 더 본다" 뒤에 "자동 교정이 끝나고도 UNCERTAIN만 남은 글은 `POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS`(기본 7일) 뒤 검수 모델로 한 번 더 보고, PASS면 `system:ai-review` 확인으로 닫는다. 그 재검수가 HARD를 내면 사람의 일이다."
**위험·롤백:** 글당 최대 1회 상위 모델 호출(표시 키로 고정), 하루 상한 공유. 롤백은 설정 `POST_PUBLISH_UNCERTAIN_REREVIEW_DAYS=0` 또는 커밋 되돌리기.

---

### Task 1-10c: `AUTO_PUBLISH_HOLD_HOSPITALS`를 준비 점검 사실과 일일 요약에 노출

**Files:**
- Modify: `backend/app/utils/production_readiness.py:321-345` (`build_report`), 새 `_auto_publish_hold_facts`
- Modify: `backend/app/services/fleet_heartbeat.py:28` (import), `:483-484` (할 일 줄)
- Test: `backend/tests/test_auto_publish_hold_visibility.py` (신규)

**조사 결과:**
- 보류 해석은 `post_publish_review_policy.auto_publish_hold()` 한 곳(`:82-83`), 발행 due 조건에서 걸러져(`:86-105`) 감시·게이트·운영센터에 "보류 중"으로도 보이지 않는다(감사 F10 확인).
- 준비 점검의 참고 사실 선례: `inquiry_sms`(`production_readiness.py:342,348-362`) — `ready`를 막지 않는다. 같은 모양으로 둔다.
- 일일 요약 "지금 할 일" 줄 목록은 `fleet_heartbeat.py:472-485`.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_auto_publish_hold_visibility.py`

```python
"""예약 자동 발행 보류를 켜 두고 잊지 않게 준비 점검과 일일 요약에 보인다(Phase 1-10)."""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.services.fleet_heartbeat import FleetFacts, build_fleet_heartbeat
from app.utils import production_readiness
from tests.test_notification_labels import _ADMIN, _NOW, _watchdog_report


def test_readiness_reports_the_hold_as_a_fact(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", f"{uuid.uuid4()},{uuid.uuid4()}")

    facts = production_readiness._auto_publish_hold_facts()

    assert facts == {"active": True, "all_hospitals": False, "hospital_count": 2}


def test_heartbeat_names_an_active_hold(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "*")

    message = build_fleet_heartbeat(
        _watchdog_report(publish_due_remaining=0, publish_missing=False),
        FleetFacts(2, 0, 0, 0, 2, 2),
        now=_NOW,
        admin_base_url=_ADMIN,
    ).message

    assert "예약 자동 발행 보류가 켜져 있습니다(전체 병원)" in message.fallback_text


def test_heartbeat_is_silent_when_no_hold(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_PUBLISH_HOLD_HOSPITALS", "")

    message = build_fleet_heartbeat(
        _watchdog_report(publish_due_remaining=0, publish_missing=False),
        FleetFacts(2, 0, 0, 0, 2, 2),
        now=_NOW,
        admin_base_url=_ADMIN,
    ).message

    assert "자동 발행 보류" not in message.fallback_text
```

`build_report` 전체 사실 키 검증은 기존 `test_build_report_requires_schema_owner_and_runtime_dependencies`(`tests/test_production_readiness.py:197-246`) 끝에 한 줄 추가:

```python
    assert report["facts"]["auto_publish_hold"]["active"] is False
```

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_auto_publish_hold_visibility.py tests/test_production_readiness.py -v` Expected: FAIL — `AttributeError: module 'app.utils.production_readiness' has no attribute '_auto_publish_hold_facts'`, `AssertionError`(요약 문구 없음), `KeyError: 'auto_publish_hold'`.

- [ ] **Step 3: 최소 구현**

(a) `production_readiness.py` `_inquiry_sms_facts` 뒤:

```python
def _auto_publish_hold_facts() -> dict[str, Any]:
    """예약 자동 발행 보류 설정 — 참고 사실이며 준비 판정을 막지 않는다.

    보류는 발행 due 조건에서 걸러져 감시·운영센터 어디에도 '보류 중'으로 보이지 않는다. 켜 두고
    잊으면 그 병원은 발행 0건이 된다. 배포 확인에서 한 번에 읽히게 둔다(병원 id는 싣지 않는다).
    """
    from app.services.post_publish_review_policy import auto_publish_hold  # noqa: PLC0415

    hold = auto_publish_hold()
    return {
        "active": hold.active,
        "all_hospitals": hold.all_hospitals,
        "hospital_count": len(hold.hospital_ids),
    }
```

`build_report`의 `"facts"`에 `"auto_publish_hold": _auto_publish_hold_facts(),` 추가.

(b) `fleet_heartbeat.py:28` import를 `from app.services.post_publish_review_policy import auto_publish_hold, publicly_operational_hospital_predicate`로 바꾸고, `actions.append(f"자동 복구 중 {facts.recovering}건은 …")`(`:485`) 바로 앞에:

```python
    hold = auto_publish_hold()
    if hold.active:
        scope = "전체 병원" if hold.all_hospitals else f"{len(hold.hospital_ids)}곳"
        actions.append(
            f"예약 자동 발행 보류가 켜져 있습니다({scope}). 보류 중인 병원의 글은 생성만 되고 "
            "발행되지 않습니다. 의도한 보류가 아니면 개발 담당에게 보류 설정 해제를 요청해 주세요."
        )
```

- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_auto_publish_hold_visibility.py tests/test_production_readiness.py tests/test_notification_labels.py tests/test_geo_autonomy_hardening.py -v` Expected: 모두 PASS.

- [ ] **Step 5: 커밋** `git add backend/app/utils/production_readiness.py backend/app/services/fleet_heartbeat.py backend/tests/test_auto_publish_hold_visibility.py backend/tests/test_production_readiness.py && git commit -m "feat(ops): 예약 자동 발행 보류를 준비 점검 사실과 일일 요약에 드러낸다"`

**계약 영향:** CLAUDE.md "검증과 배포" 절에 "배포 후 readiness `facts.auto_publish_hold.active`가 의도와 맞는지 확인한다." 추가.
**위험·롤백:** 낮음. 문구만 늘어난다. 롤백은 커밋 되돌리기.

---

## 배포 후 검증 쿼리 (운영 DB 읽기 전용)

```sql
-- 1) 사람의 일 0~2건 목표: 기한 없는 RETRYING이 남지 않았는가(MISSING_APPROVED_ESSENCE 제외)
SELECT incident_type, count(*) FROM incidents
 WHERE state='RETRYING' AND sla_due_at IS NULL
   AND NOT (incident_type='CONTENT_GENERATION_FAILED' AND safe_error_code='MISSING_APPROVED_ESSENCE')
 GROUP BY 1;   -- 기대: 0행
-- 2) 7일 넘게 OPEN인 사고 종류별
SELECT incident_type, count(*) FROM incidents
 WHERE state='OPEN' AND first_seen_at < now() - interval '7 days' GROUP BY 1 ORDER BY 2 DESC;
-- 3) 보류·종결된 알림
SELECT safe_error_code, notification_type, count(*) FROM notification_outbox
 WHERE safe_error_code='INCIDENT_RESOLVED_BEFORE_SEND' GROUP BY 1,2;
-- 4) 정착이 닫은 수(24h)
SELECT action, count(*) FROM admin_audit_logs
 WHERE action IN ('incident_resolved_by_later_evidence','incident_closed_by_linked_success',
                  'incident_auto_acknowledged','incident_retry_deadline_defaulted')
   AND created_at > now() - interval '1 day' GROUP BY 1;
```

## 대표 결정이 필요한 질문

1. **1-10a**: 2026-10-08 "사후검수 지적 Slack 끔" 지시의 예외로, 자동 교정이 끝나고도 HARD 지적이 남은 공개 글만 Slack·운영자 할 일로 올려도 되는가.
2. **1-8 COST_GUARD_LIMIT_REACHED**: 상한·킬스위치 조정은 비용 결정이다. AE 큐에서 빼서 "개발 확인"으로 보내는 것이 맞는가, 아니면 대표(OWNER) 몫으로 운영 큐에 남겨야 하는가.
3. **1-1 MONTHLY_SOV_MEASUREMENT_FAILED**: 창이 열린 24일 실패도 다음 달 7일까지(최대 14일) 사람의 일이 아니다. 결정적 실패(질문 세트 부족 등)는 더 일찍 넘길지(예: 원인 코드별 72시간 상한).
4. **1-8 SOURCE_PROCESSING_FAILED**: 감사는 강등을 제안했지만 유일한 신호라 남겼다. 상태 카드 확장 후 강등할지.
