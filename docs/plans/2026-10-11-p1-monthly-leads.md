# Phase 1 — 월간 오탐·코호트 공백·V0/리드/도입문의 자동 재실행 (감사 §4 1-6 · 1-7 · 1-9)

기준 소스: main `8c8f6e60`. 모든 줄 번호는 이 커밋 기준 실측값이다(감사 문서의 줄 번호와 다른 곳은 본문에 명시).
공통 실행 규칙: `cd /Users/woojinlee/Documents/projects/reputation/backend`. 순수 단위 테스트는 DB 변수 없이
돈다. `tests/integration/*`은 `testenv-and-queries.md` Part A의 export 블록(특히 `INTEGRATION_DATABASE_URL`)과
`alembic upgrade head`가 끝난 DB가 필요하다. 커밋은 브랜치에서 한다(`git switch -c fix/p1-monthly-autonomy`).

## 조사 요약 — 감사와 다른 점

1. **1일 사고 위치**: `run_monthly_reports`의 `_record_weekly_sov_failure(... "MONTHLY_SOV_MEASUREMENT_INCOMPLETE")`는
   `tasks.py:12338-12344`(감사 12317-12324). 복구 마감 함수는 `tasks.py:8844-8848 _monthly_sov_recovery_deadline` =
   `reporting_period(y,m).ends_at + 7d − 1s` = **다음 달 7일 23:59:59 KST**.
2. **감사가 놓친 후속 결함**: 8일 00:15 최종화 뒤(`_monthly_sov_measurement_succeeded`가 마감 뒤 LIMITED/UNAVAILABLE을
   최종으로 인정, `tasks.py:11218-11224`) 아무도 `MONTHLY_SOV_MEASUREMENT_FAILED`를 닫지 않는다. 백로그 해결 규칙
   `_same_period_measurement`(`incident_backlog.py:52-79`)는 `RUN_SOV SUCCEEDED`만 근거로 보는데, 마감으로 닫힌 달에는
   새 RUN_SOV가 없다. RETRYING으로 바꾸기만 하면 8일 0시에 기한이 지나 그대로 "사람의 일"이 된다 → Task 1-6a에서
   8일 최종화 때 기계가 닫도록 함께 고친다.
3. **1-6(c) 운영 env**: `SOV_MONTHLY_COHORT_LIMIT`은 운영 서비스 env에 명시돼 있다
   (`docs/releases/2026-10-01-report-plain-language-production.md:30`). 기본값만 올리면 운영에 반영되지 않는다 →
   설정 필드를 없애고 `COST_GUARD_MONTHLY_SOV_QUERIES // (15×2×5)`로 계산한다(Settings는 `extra="ignore"`라
   남은 env는 무해, `config.py:133-135`). 4260 기준 경고선은 **28곳**.
4. **1-7 "SovRecord 없는 병원 즉시 측정"**: 주간 배치(`run_weekly_monitoring`, `tasks.py:10010-10066`)는 이미
   코호트 밖 ACTIVE 병원을 **매주 측정한다**(SovRecord가 없어도). V0도 관측 슬롯으로 SovRecord를 만든다
   (`tasks.py:4748` → `_execute_paid_observation_slot` → `tasks.py:9663`). 그래서 "기록 없음"은 대부분 ①24일 직전에
   활성화된 병원 ②V0 실패 병원이다. 주간 실행에서 "기록 없음"을 사고로 올리면 같은 실행이 곧 측정하므로 **오탐**이다
   → 주간에는 세트 부족 같은 사람 몫 사유만 올리고, **월말 창(24일~) tick에서** 기록 없는 병원에 1회 측정을 보내고
   그 공백 사고는 RETRYING(24h)으로 연다(Task 1-7b).
5. **1-9(a) V0 체크포인트 창**: 재사용 가능한 완료 측정은 `completed_at`이 **6시간 이내**일 때만 찾는다
   (`v0_checkpoint.py:41,138-160`). 6시간 간격 재실행은 완료된 150건 측정을 **다시 산다**(PDF 단계 실패 후 재실행).
   → 같은 lineage(같은 OperationRun id)일 때만 창을 7일(`V0_RESUME_MAX_AGE_SECONDS`)로 넓힌다.
6. **1-9(a) 이중 사고**: V0 최종 실패는 `V0_REPORT_FAILED`(`tasks.py:5011-5036`)를 열고 `raise exc`한다. 그러면
   `track_operation_failure`가 실행을 `FAILED/TASK_FAILED`로 끝내고(`operation_run_signals.py:164-180`),
   `record_task_failure`가 `terminal_outcome_identity`(`TASK_FAILED`는 분류 소유자 예외에서 빠짐,
   `operation_terminal_outcomes.py:79-81`)로 **`OPERATION_TERMINAL_FAILED`를 하나 더 열고 Slack을 보낸다**
   (`task_incident_control.py:82-117`). Task 1-9a에서 최종 실패 전에 실행을 `V0_REPORT_FAILED` 코드로 명시 종결해 막는다.
7. **1-9(a) V0 측정 실패의 실체**: 관측 슬롯 경로는 공급자 장애여도 LIMITED/UNAVAILABLE로 **보고서를 완성**한다
   (`tasks.py:4780-4795`). `V0_REPORT_FAILED`는 실제로는 PDF·GCS·DB 같은 측정 이후 단계 실패(재시도 2회 소진)와
   `V0MeasurementPolicyDrift`(배포 기준 변경, 재실행으로 안 풀림)에서 난다. 재실행 대상에서 PolicyDrift는 뺀다.
8. **1-9(c) 도입문의 Slack**: drain이 집는 조건은 `source == AI_DIAGNOSIS`뿐이다(`lead_diagnosis_tasks.py:676-690`,
   claim도 `:108`). 도입문의(`source="INQUIRY"`)는 상태를 PENDING으로 바꿔도 **drain이 영원히 안 집는다** → claim·선택
   조건과 발송 함수까지 같이 바꿔야 한다. 도입문의는 접수 API가 Slack을 보내는 동안 상태가 NULL이므로 drain은
   INQUIRY의 NULL을 집으면 안 된다(중복 발송).
9. **1-9(c) SMS**: `deliver`는 `httpx.HTTPError`만 잡아 FAILED로 돌려준다(`inquiry_sms.py:157-180`) — "예외를 삼킨다"는
   맞다. 재시도는 **요청이 사업자에 닿지 않은 경우(ConnectError·ConnectTimeout)와 429/503만** 1회 한다. 읽기 시간 초과·
   5xx 일반은 이미 발송됐을 수 있어 재시도하면 원장 휴대전화에 문자가 두 번 간다.
10. **레지스트리 연동**: 다른 에이전트(draft-p1-incidents)가 사고 유형 레지스트리 `{audience, quiet, initial_state,
    deadline_fn, resolver}`를 설계 중이다. 이 문서의 모든 변경은 단독으로 동작하며, 각 Task 끝의 "레지스트리 항목"에
    그대로 옮길 수 있는 형태(순수 함수 `deadline_fn(error_code, source_id, now)`, `resolver(db, incident, now)`)로 둔다.

## Task 순서와 의존

| Task | 내용 | 의존 |
|---|---|---|
| 1-6a | 월간 측정 사고: 복구 마감까지 RETRYING, 8일 최종화 때 기계가 닫음 | 없음 |
| 1-6b | 공백 요약 1~8일, '자동 복구 종료'를 8일 최종화 뒤로 | 없음 |
| 1-7a | 공백 판정을 전달 게이트로, `MANIFEST_MISSING` 별도 상태·정직한 안내 | 1-6b |
| 1-6c | 코호트 비용 경고선을 비용 한도에서 계산, OVER_LIMIT 월말 자동 닫힘 | 없음 |
| 1-7b | 주간 실행의 코호트 공백 보고, 월말 창의 기록 없는 병원 1회 측정 | 1-6c(같은 파일 인접) |
| 1-9a | V0_REPORT_FAILED 6h×3 자동 재실행, 이중 사고 제거 | 없음 |
| 1-9b | 리드 진단 실행 재시도 물러서기(즉시·1h·2h·4h, 5회) | 없음 |
| 1-9c-1 | 도입문의 Slack 실패를 PENDING으로, drain이 1시간 재시도 후 사고 | 1-9b(같은 파일) |
| 1-9c-2 | 도입문의 SMS 1회 재시도 + 최종 실패 사고 | 1-9c-1(같은 해결 규칙) |

RedBeat 버전은 1-6b에서 한 번만 올린다. 다른 Phase 작업과 같은 릴리스면 버전 문자열 충돌을 병합 때 하나로 맞춘다.

---

### Task 1-6a: 월간 측정 사고를 복구 마감까지 RETRYING으로 열고, 8일 최종화 때 기계가 닫는다

**Files:**
- Modify: `backend/app/services/monthly_period.py:117-127` (뒤에 `monthly_recovery_deadline` 추가)
- Modify: `backend/app/workers/tasks.py:8844-8848` (`_monthly_sov_recovery_deadline`가 서비스 함수 위임)
- Modify: `backend/app/workers/weekly_sov_incident_control.py:1-50, 187-280`
- Modify: `backend/app/workers/tasks.py:12273-12280` (`run_monthly_reports` 루프), `:9324-9334` 근처에 헬퍼 추가
- Test: `backend/tests/test_monthly_sov_incident_retrying.py`(신규), `backend/tests/test_sov_repeat_alert_cut.py`(추가)

**조사 결과:**
- 1일 00:15 `run_monthly_reports`가 측정 미완료 코호트 병원마다 `_record_weekly_sov_failure(..., "MONTHLY_SOV_MEASUREMENT_INCOMPLETE",
  measurement_mode="monthly")`(`tasks.py:12338-12344`) → `open_monthly_sov_failure` → `_open_sov_failure`
  (`weekly_sov_incident_control.py:187-280`)가 `open_or_touch_incident`(항상 OPEN, `incidents.py:79-81,103`)로 HIGH·OPEN을 열고
  Slack(`:249-279`)을 보낸다. 같은 날 09:00 공백 요약은 "수동으로 반복 실행하지 마세요"라고 말한다(모순).
- 월간 모드 오류 코드는 `failure_prefix="MONTHLY_SOV"`(`tasks.py:8211`) + 아래 6종이다. 기계가 소유한 것(1~7일 catch-up과
  24~말일 6시간 tick이 실제로 다시 사는 것)과 사람 몫을 가른다.
  - 기계: `MEASUREMENT_PARTIAL`(`:8479,8612,8664,8690`), `MEASUREMENT_INCOMPLETE`(`:8962,12338`), `COST_GUARD_BLOCKED`(`:8507,8805`)
  - 사람: `NO_MEASUREMENT_MANIFEST`(`:8349`), `MEASUREMENT_POLICY_DRIFT`(`:8332,8412`), `UNRESOLVED_MANIFEST_STATE`(`:8391`)
- 사람의 일 술어는 RETRYING+기한 내를 사람의 일로 세지 않는다(`operator_action.py:9-19`). 패턴은 콘텐츠 생성과 같다
  (`generation_incident_control.py:1212-1225`: `mark_retrying` 후 `incident.sla_due_at = deadline`).
- `_recover_sov_failure`(`weekly_sov_incident_control.py:299-337`)는 OPEN·RETRYING 모두 닫는다. 8일 최종화 뒤 호출하면 된다.
- 기존 테스트 `test_cost_guard_blocked_opens_incident_without_slack`·`test_human_action_measurement_failure_still_slacks`
  (`tests/test_sov_repeat_alert_cut.py:782-891`)는 `period_key="2026-08"`이라 오늘 기준 마감이 지나 OPEN 경로로 남는다(그대로 통과).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_monthly_sov_incident_retrying.py`

```python
"""월초 1~7일 catch-up이 소유한 측정 실패는 사람의 일이 아니다(2026-10 감사 F5-①)."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.operations import Incident
from app.services.monthly_period import KST, monthly_recovery_deadline
from app.workers import weekly_sov_incident_control as control

DEADLINE_SEP = datetime(2026, 10, 7, 23, 59, 59, tzinfo=KST)


def test_recovery_deadline_is_the_last_second_of_day_seven_kst():
    assert monthly_recovery_deadline(2026, 9) == DEADLINE_SEP
    assert monthly_recovery_deadline(2026, 12) == datetime(2027, 1, 7, 23, 59, 59, tzinfo=KST)


@pytest.mark.parametrize(
    "code",
    [
        "MONTHLY_SOV_MEASUREMENT_INCOMPLETE",
        "MONTHLY_SOV_MEASUREMENT_PARTIAL",
        "MONTHLY_SOV_COST_GUARD_BLOCKED",
    ],
)
def test_machine_owned_failures_retry_until_the_recovery_deadline(code):
    day_one = datetime(2026, 10, 1, 0, 20, tzinfo=KST)
    window = datetime(2026, 9, 26, 6, 0, tzinfo=KST)

    assert control.monthly_sov_retry_deadline(code, "2026-09", day_one) == DEADLINE_SEP
    assert control.monthly_sov_retry_deadline(code, "2026-09", window) == DEADLINE_SEP


@pytest.mark.parametrize(
    "code",
    [
        "MONTHLY_SOV_NO_MEASUREMENT_MANIFEST",
        "MONTHLY_SOV_MEASUREMENT_POLICY_DRIFT",
        "MONTHLY_SOV_UNRESOLVED_MANIFEST_STATE",
    ],
)
def test_human_failures_open_immediately(code):
    assert control.monthly_sov_retry_deadline(
        code, "2026-09", datetime(2026, 10, 1, 0, 20, tzinfo=KST)
    ) is None


def test_after_the_deadline_nothing_is_machine_owned():
    assert control.monthly_sov_retry_deadline(
        "MONTHLY_SOV_MEASUREMENT_INCOMPLETE", "2026-09", datetime(2026, 10, 8, 0, 15, tzinfo=KST)
    ) is None


def test_malformed_period_is_not_machine_owned():
    assert control.monthly_sov_retry_deadline(
        "MONTHLY_SOV_MEASUREMENT_INCOMPLETE", "2026-W40", datetime(2026, 10, 1, tzinfo=KST)
    ) is None


class _DB:
    def __init__(self, previous_state):
        self.previous_state = previous_state

    async def scalar(self, _stmt):
        if self.previous_state is None:
            return None
        return SimpleNamespace(state=self.previous_state)

    async def commit(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _patch(monkeypatch, *, now, previous_state=None):
    opened, retried, enqueued = [], [], []

    async def fake_open(db, request, **_kwargs):
        opened.append(request)
        return Incident(
            id=uuid.uuid4(), state="OPEN", severity=request.severity.value, version=1,
            episode_seq=1, customer_impact=request.customer_impact,
            next_action=request.next_action, admin_path=request.admin_path,
            hospital_id=request.hospital_id, operation_run_id=request.operation_run_id,
            safe_error_message=request.safe_error_message,
            incident_type=request.incident_type,
        )

    async def fake_retrying(db, incident_id, *, expected_version, actor, reason):
        retried.append(incident_id)
        return Incident(id=incident_id, state="RETRYING", severity="MEDIUM", version=2)

    async def fake_enqueue(*_args, **_kwargs):
        enqueued.append(True)

    monkeypatch.setattr(control, "_now", lambda: now)
    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: (lambda: _DB(previous_state)))
    monkeypatch.setattr(control, "open_or_touch_incident", fake_open)
    monkeypatch.setattr(control, "mark_retrying", fake_retrying)
    monkeypatch.setattr(control, "enqueue_notification", fake_enqueue)
    return opened, retried, enqueued


async def test_day_one_incomplete_measurement_is_retrying_with_deadline_and_no_slack(monkeypatch):
    opened, retried, enqueued = _patch(monkeypatch, now=datetime(2026, 10, 1, 0, 20, tzinfo=KST))

    await control.open_monthly_sov_failure(
        hospital_id=uuid.uuid4(), hospital_name="테스트의원", period_key="2026-09",
        error_code="MONTHLY_SOV_MEASUREMENT_INCOMPLETE",
    )

    assert opened[0].severity.value == "MEDIUM"
    assert len(retried) == 1
    assert enqueued == []


async def test_human_code_after_a_retrying_episode_still_slacks(monkeypatch):
    _opened, retried, enqueued = _patch(
        monkeypatch, now=datetime(2026, 10, 2, tzinfo=KST), previous_state="RETRYING"
    )

    await control.open_monthly_sov_failure(
        hospital_id=uuid.uuid4(), hospital_name="테스트의원", period_key="2026-09",
        error_code="MONTHLY_SOV_NO_MEASUREMENT_MANIFEST",
    )

    assert retried == []
    assert enqueued == [True]
```

`backend/tests/test_sov_repeat_alert_cut.py` 맨 끝에 추가:

```python
def test_day_eight_finalize_closes_the_month_measurement_incident(monkeypatch):
    """마감 뒤 LIMITED/UNAVAILABLE도 최종이다 — 그 달 측정 사고는 기계가 닫는다."""
    hospital = SimpleNamespace(id=uuid.uuid4(), name="최종화의원", monthly_sov_cohort=True)
    _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 10, 8, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids={hospital.id},
    )
    recovered = []
    monkeypatch.setattr(
        tasks,
        "_recover_sov_failure",
        lambda **kwargs: recovered.append(kwargs) or "coroutine",
    )
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)

    tasks.run_monthly_reports.run()

    assert recovered == [
        {"hospital_id": hospital.id, "period_key": "2026-09", "measurement_mode": "monthly"}
    ]


def test_pending_measurement_is_not_closed_by_the_batch(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), name="미완료의원", monthly_sov_cohort=True)
    _patch_monthly_report_batch(
        monkeypatch,
        [hospital],
        now=tasks.arrow.get(2026, 10, 2, 0, 15, tzinfo="Asia/Seoul"),
        succeeded_ids=set(),
    )
    monkeypatch.setattr(
        tasks, "_recover_sov_failure", lambda **_kwargs: pytest.fail("must not close")
    )

    tasks.run_monthly_reports.run()
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_sov_incident_retrying.py tests/test_sov_repeat_alert_cut.py -k "deadline or retrying or machine or human or malformed or finalize or pending_measurement" -v`
  Expected: `ImportError: cannot import name 'monthly_recovery_deadline'` (수집 단계 실패), 추가한 두 테스트는 `assert recovered == [...]` 실패.

- [ ] **Step 3: 최소 구현**

`backend/app/services/monthly_period.py` — `is_monthly_recovery_window` 바로 뒤(127행 다음)에 추가:

```python
def monthly_recovery_deadline(year: int, month: int) -> datetime:
    """그 달 측정을 자동 복구가 소유하는 마지막 시각 — 다음 달 7일 23:59:59 KST.

    8일 00:15 월간 배치는 이 시각이 지난 고정 사실로 LIMITED/UNAVAILABLE을 최종화한다.
    """

    return reporting_period(year, month).ends_at + timedelta(days=MONTHLY_RECOVERY_END_DAY) - timedelta(
        seconds=1
    )
```

`backend/app/workers/tasks.py:8844-8848`을 위임으로 바꾼다(동작 동일, 정의 하나):

```python
def _monthly_sov_recovery_deadline(year: int, month: int) -> datetime:
    """Return the inclusive KST service-period recovery horizon."""

    return monthly_recovery_deadline(year, month)
```
그리고 `from app.services.monthly_period import (...)` 블록에 `monthly_recovery_deadline`을 추가한다
(`grep -n "from app.services.monthly_period import" app/workers/tasks.py`로 위치 확인).

`backend/app/workers/weekly_sov_incident_control.py`:
- import에 `from app.services.monthly_period import monthly_recovery_deadline` 추가.
- `_SOURCE_TYPE` 정의 아래에 추가:

```python
# 1~7일 catch-up과 24~말일 6시간 tick이 실제로 다시 사는 실패만 기계 소유다. 측정 기준·질문 세트가
# 없는 실패는 사람이 입력을 바꿔야 풀리므로 처음부터 OPEN이다.
MONTHLY_MACHINE_OWNED_CODES = frozenset(
    {
        "MONTHLY_SOV_MEASUREMENT_PARTIAL",
        "MONTHLY_SOV_MEASUREMENT_INCOMPLETE",
        "MONTHLY_SOV_COST_GUARD_BLOCKED",
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def monthly_sov_retry_deadline(
    error_code: str, period_key: str, now: datetime
) -> datetime | None:
    """기계가 이 월간 측정 실패를 소유하는 기한. None이면 지금 사람의 일이다.

    사고 유형 레지스트리의 `deadline_fn`으로 그대로 옮길 수 있게 순수 함수로 둔다.
    """

    if error_code not in MONTHLY_MACHINE_OWNED_CODES:
        return None
    try:
        year_text, month_text = period_key.split("-", 1)
        deadline = monthly_recovery_deadline(int(year_text), int(month_text))
    except (AttributeError, TypeError, ValueError):
        return None
    return deadline if now < deadline else None
```

- `_open_sov_failure`(187-280) 변경 — `observed_at = datetime.now(UTC)`를 `observed_at = _now()`로 바꾸고, `open_or_touch_incident`
  호출 직전에 기한을 계산해 심각도·상태·Slack을 가른다. 바뀌는 부분 전체:

```python
    observed_at = _now()
    ...
    retry_until = (
        monthly_sov_retry_deadline(error_code, period_key, observed_at) if monthly else None
    )
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        key = _dedupe_key(hospital_id, period_key, monthly=monthly)
        previous = await db.scalar(select(Incident).where(Incident.dedupe_key == key))
        previous_state = previous.state if previous is not None else None
        incident = await open_or_touch_incident(
            db,
            IncidentOpenRequest(
                ...  # 기존 인자 그대로, severity만 아래로 교체
                severity=(
                    IncidentSeverity.MEDIUM if retry_until is not None else IncidentSeverity.HIGH
                ),
                ...
            ),
            actor=actor,
            reason=f"{pipeline} visibility measurement failed",
            now=observed_at,
        )
        if retry_until is not None:
            retrying = await mark_retrying(
                db,
                incident.id,
                expected_version=incident.version,
                actor=actor,
                reason="monthly catch-up owns recovery until the recovery deadline",
            )
            if isinstance(retrying, Incident):
                incident = retrying
                incident.sla_due_at = retry_until
        if transition is not None:
            ...  # 기존 그대로
        elif (
            retry_until is None
            and not error_code.endswith("COST_GUARD_BLOCKED")
            and (
                previous_state is None
                or previous_state
                in {
                    IncidentState.RECOVERED.value,
                    IncidentState.ACKNOWLEDGED.value,
                    # 기계가 들고 있던 에피소드가 사람 몫 원인으로 바뀌면 처음 사람에게 알린다.
                    IncidentState.RETRYING.value,
                }
            )
        ):
            ...  # 기존 enqueue 그대로
```

`backend/app/workers/tasks.py` — `_recover_sov_failure`(9324-9334) 바로 아래에 추가:

```python
def _close_finalized_monthly_sov_incident(hospital: Hospital, period_key: str) -> None:
    """측정이 최종(COMPLETE, 또는 마감 뒤 LIMITED/UNAVAILABLE)이면 그 달 측정 사고는 기계가 닫는다.

    마감으로 닫힌 달에는 새 RUN_SOV 성공이 없어 백로그 해결 규칙이 근거를 찾지 못한다.
    닫지 않으면 8일 0시에 RETRYING 기한이 지나 이미 전달 가능한 달이 사람의 일로 남는다.
    """
    try:
        _run_async(
            _recover_sov_failure(
                hospital_id=hospital.id, period_key=period_key, measurement_mode="monthly"
            )
        )
    except Exception:
        logger.exception(
            "Finalized monthly measurement incident could not be closed",
            extra={"hospital_id": str(hospital.id), "period_key": period_key},
        )
```

`run_monthly_reports` 루프(`tasks.py:12273-12280`)를 아래로 교체:

```python
        for hospital in eligible_hospitals:
            if _hospital_requires_monthly_sov_success(db, hospital, period):
                if not _monthly_sov_measurement_succeeded(db, hospital.id, period_key):
                    blocked.append(hospital.name)
                    measurement_pending.append(hospital)
                    continue
                _close_finalized_monthly_sov_incident(hospital, period_key)
            latest_run = _latest_monthly_report_operation_run(
```
(그 아래 `latest_run = ...`부터는 그대로.)

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_sov_incident_retrying.py tests/test_sov_repeat_alert_cut.py tests/test_monthly_period.py -q` (마지막 파일이 없으면 생략)
  Expected: 전부 PASS. 이어서 `.venv/bin/python -m pytest tests/test_sov_monthly_redesign.py tests/test_sov_monthly_chunk_continuation.py -q` → PASS(회귀 없음).

- [ ] **Step 5: 커밋**
  `git add backend/app/services/monthly_period.py backend/app/workers/weekly_sov_incident_control.py backend/app/workers/tasks.py backend/tests/test_monthly_sov_incident_retrying.py backend/tests/test_sov_repeat_alert_cut.py && git commit -m "fix(monthly-sov): 월초 catch-up이 소유한 측정 실패는 복구 마감까지 RETRYING, 8일 최종화가 닫는다"`

**레지스트리 항목(1-1 연동):** `MONTHLY_SOV_MEASUREMENT_FAILED: initial_state=RETRYING if deadline_fn(...) else OPEN,
deadline_fn=monthly_sov_retry_deadline(error_code, period_key, now), resolver=_same_period_measurement + 8일 최종화(명시 호출)`.
레지스트리가 들어오면 `_open_sov_failure`의 `mark_retrying` 분기만 레지스트리 호출로 바뀐다.

**계약 영향:** CLAUDE.md "자율 작업·알림·비용"에 한 문장 추가 — "월간 측정 실패 중 부분·미완료·비용 보류는 다음 달 7일
23:59:59 KST(복구 마감)까지 RETRYING이고 Slack을 보내지 않는다. 8일 00:15 최종화가 그 달 측정 사고를 닫는다. 질문 세트·
측정 기준 문제는 처음부터 OPEN이다."

**위험·롤백:** 1~7일 동안 진짜 막힌 측정(예: 공급자 장기 장애)도 7일 밤까지 사람 큐에 안 보인다 — 1~7일 09:00 공백
요약(AUTO, Report)이 같은 사실을 매일 알리므로 정보는 남는다. 롤백은 커밋 revert(스키마 변경 없음).

---

### Task 1-6b: 공백 요약을 1~8일로 늘리고 '자동 복구 종료'를 8일 최종화 뒤로 옮긴다

**Files:**
- Modify: `backend/app/services/monthly_period.py:21` (상수 추가)
- Modify: `backend/app/services/monthly_report_gap_notifications.py:1-9, 132-150, 159-170, 203-246`
- Modify: `backend/app/core/celery_app.py:35, 434-440`
- Test: `backend/tests/test_operator_channel_signal.py:198-210`, `backend/tests/test_monthly_report_gap_notifications.py:26-47`, `backend/tests/test_autonomous_loop_recovery.py:181-183`

**조사 결과:**
- 공백 요약 Beat는 `day_of_month="1-7"` 09:00(`celery_app.py:436-440`), 함수도 1~7일만 돈다(`monthly_report_gap_notifications.py:239`).
- `monthly_report_gap_intents`의 주석 "마지막 복구일(7일)에는 이튿날 00:15 배치가 없다"(`:223`)는 **틀렸다** — `monthly-reports`는
  `day_of_month="1-8"`(`celery_app.py:430-434`)이고 8일 tick이 마감 사실로 최종화한다(`tasks.py:12267-12270`, `:11218-11224`).
  그래서 7일 09:00 "[조치 필요] 자동 복구 종료"는 몇 시간 뒤 8일 00:15가 푸는 공백을 사람에게 넘긴다.
- RedBeat 스케줄 변경은 영속 스케줄 재조정이 필요하다(`REDBEAT_SCHEDULE_VERSION = "2026-10-09.1"`, `celery_app.py:35`,
  `utils/reconcile_redbeat_schedule.py:92-121`).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_operator_channel_signal.py:198-210`의 `test_last_recovery_day_closes_still_open_auto_gaps_with_one_error`를 교체:

```python
def test_day_seven_is_still_automatic_and_day_eight_closes_with_one_error() -> None:
    open_auto = [
        gaps.MonthlyReportGap("A의원", "MISSING", recoverable=True),
        gaps.MonthlyReportGap("B의원", "COVERAGE_INCOMPLETE", recoverable=True),
    ]

    day_seven = gaps.monthly_report_gap_intents(period_key="2026-09", gaps=open_auto, day=7)
    day_eight = gaps.monthly_report_gap_intents(period_key="2026-09", gaps=open_auto, day=8)

    # 7일 밤 23:59:59까지 복구가 돌고 8일 00:15가 최종화한다 — 7일은 아직 사람의 일이 아니다.
    assert [intent.notification_type for intent in day_seven] == ["MONTHLY_REPORT_GAP_AUTO"]
    assert [intent.notification_type for intent in day_eight] == ["MONTHLY_REPORT_GAP_SUMMARY"]
    [closing] = day_eight
    assert closing.message.fallback_text.startswith(ERROR_LABEL)
    assert "자동 복구 종료" in closing.message.fallback_text
    assert _AUTO_PROMISE not in closing.message.fallback_text
    assert closing.dedupe_key.endswith(":closed")
```

`backend/tests/test_monthly_report_gap_notifications.py:26-47`의 `test_gap_summary_is_edge_limited_to_days_one_through_seven`을 교체:

```python
def test_gap_summary_runs_days_one_through_eight(monkeypatch):
    loaded = []
    monkeypatch.setattr(
        notifications,
        "load_monthly_report_gaps",
        lambda *_args, **_kwargs: loaded.append(True) or ("2026-08", []),
    )
    monkeypatch.setattr(
        notifications, "enqueue_onboarding_notification_sync", lambda *_a, **_k: None
    )

    # UTC 9/8 00:00 = KST 9/8 09:00 — 8일 최종화 뒤의 마지막 판정 날이다.
    notifications.enqueue_monthly_report_gap_summary_sync(object(), now=datetime(2026, 9, 8, tzinfo=UTC))
    # UTC 9/9 00:00 = KST 9/9 09:00 — 더 돌지 않는다.
    notifications.enqueue_monthly_report_gap_summary_sync(object(), now=datetime(2026, 9, 9, tzinfo=UTC))

    assert loaded == [True]
```

`backend/tests/test_autonomous_loop_recovery.py:181-183`의 기대값을 `"<crontab: 0 9 1-8 * * (m/h/dM/MY/d)>"`로 바꾸고,
`backend/tests/test_celery_routing.py` 맨 끝에 추가:

```python
def test_gap_summary_runs_after_the_day_eight_finalize():
    schedule = celery_app.conf.beat_schedule["monthly-report-gap-summary"]["schedule"]
    assert schedule.day_of_month == set(range(1, 9))
    assert schedule.hour == {9}
    assert REDBEAT_SCHEDULE_VERSION >= "2026-10-12.1"
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_operator_channel_signal.py::test_day_seven_is_still_automatic_and_day_eight_closes_with_one_error tests/test_monthly_report_gap_notifications.py tests/test_autonomous_loop_recovery.py tests/test_celery_routing.py::test_gap_summary_runs_after_the_day_eight_finalize -v`
  Expected: day_seven이 `MONTHLY_REPORT_GAP_SUMMARY`를 내서 FAIL, 9/8 판정이 로드되지 않아 `loaded == []` FAIL, crontab 문자열 FAIL, `day_of_month` FAIL.

- [ ] **Step 3: 최소 구현**

`backend/app/services/monthly_period.py:21` 아래:

```python
# 7일 23:59:59까지 자동 복구, 8일 00:15 월간 배치가 마감 사실로 최종화한다. 공백의 최종 판정은 이날 한다.
MONTHLY_FINALIZE_DAY = MONTHLY_RECOVERY_END_DAY + 1
```

`backend/app/services/monthly_report_gap_notifications.py`:
- import를 `from app.services.monthly_period import MONTHLY_FINALIZE_DAY, MONTHLY_RECOVERY_END_DAY, eligible_hospital_ids, prior_month_to_close`로.
- 모듈 docstring 7-8행을 교체: `- 8일 00:15 최종화 뒤(8일 09:00)에도 남은 AUTO 공백: 더 돌 자동 복구가 없으므로 '자동 복구 종료' ERROR 한 건.`
- `build_monthly_report_gap_auto_summary`의 detail 문구(`:144-147`)를 교체:

```python
        detail=(
            f"대상: {_names(gaps)}\n"
            "시스템이 매월 1~7일 자동 재측정·마감을 계속하고 8일 00:15에 최종 마감합니다. "
            "수동으로 반복 실행하지 마세요. 최종 마감 뒤에도 남은 공백은 8일 오전에 별도 조치 알림으로 안내합니다."
        ),
```
- `monthly_report_gap_intents`(`:217-235`)의 판정 두 줄을 교체:

```python
    # 7일 23:59:59까지 자동 복구가 돌고 8일 00:15 배치가 마감 사실로 최종화한다.
    # 8일 09:00 판정은 그 최종화 뒤라, 이때 남은 공백만 자동 복구가 끝난 것이다.
    recovery_ahead = day <= MONTHLY_RECOVERY_END_DAY
```
- `enqueue_monthly_report_gap_summary_sync`(`:237-246`)의 범위:

```python
    if not 1 <= local.day <= MONTHLY_FINALIZE_DAY:
        return False
```

`backend/app/core/celery_app.py`:
- `:35` → `REDBEAT_SCHEDULE_VERSION = "2026-10-12.1"`
- `:435-440`:

```python
        # 1~7일 자동 복구가 돈 뒤 남은 미생성/측정 미완료를 하루 한 번 요약하고, 8일 00:15 최종화 뒤
        # 09:00에 마지막으로 판정한다(그때 남은 공백만 '자동 복구 종료').
        "monthly-report-gap-summary": {
            "task": "app.workers.tasks.summarize_monthly_report_gaps",
            "schedule": crontab(hour=9, minute=0, day_of_month="1-8"),
            "options": {"headers": build_dispatch_headers("monthly-report-gap-summary")},
        },
```

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_operator_channel_signal.py tests/test_monthly_report_gap_notifications.py tests/test_autonomous_loop_recovery.py tests/test_celery_routing.py tests/test_notification_labels.py -q`
  Expected: 전부 PASS (`test_operator_channel_signal.py`의 DB 테스트는 SQLite 픽스처라 변수 불필요).

- [ ] **Step 5: 커밋**
  `git add backend/app/services/monthly_period.py backend/app/services/monthly_report_gap_notifications.py backend/app/core/celery_app.py backend/tests/test_operator_channel_signal.py backend/tests/test_monthly_report_gap_notifications.py backend/tests/test_autonomous_loop_recovery.py backend/tests/test_celery_routing.py && git commit -m "fix(monthly-gap): '자동 복구 종료'는 8일 00:15 최종화 뒤에만 판정한다"`

**계약 영향:** CLAUDE.md "알림" 절의 "월간 공백은 자동 복구 중(AUTO, Report)과 사람이 정할 것(MANUAL, 조치 필요)으로 가르고"
뒤에 "자동 복구 종료 판정은 8일 00:15 최종화 뒤 8일 09:00 한 번이다"를 덧붙인다.

**위험·롤백:** 배포 직후 RedBeat 재조정(`deployment-runbook.md`의 reconcile 단계)을 빠뜨리면 영속 스케줄이 1~7일로 남는다 —
배포 기록에 `reconcile_redbeat_schedule` 결과(version 2026-10-12.1)를 남긴다. 롤백은 revert + 재조정.

---

### Task 1-7a: 공백 판정을 전달 게이트로 하고 `MANIFEST_MISSING`을 정직하게 안내한다

**Files:**
- Modify: `backend/app/services/monthly_report_gap_notifications.py:22-31, 46-97` (분류·로더), 새 빌더 추가, `:203-235`
- Test: `backend/tests/test_monthly_report_gap_notifications.py`(추가)

**조사 결과:**
- 공백 로더는 `coverage_is_final`만 본다(`monthly_report_gap_notifications.py:80-96`). 전달 게이트는 **매니페스트도** 요구한다
  (`monthly_report_delivery.py:323-328`: `not coverage_is_final(report) or manifest is None` → `coverage_incomplete`).
- 코호트 밖 병원은 주간 자료로 보고서가 만들어져 `coverage_is_final`이 참일 수 있지만 `manifest_id`가 없어 **영구히 전달 불가**다.
  로더는 이 병원을 공백으로 보지 않거나(최종 커버리지), `COVERAGE_INCOMPLETE recoverable=False`로 "‘리포트 다시 만들기’를
  눌러 주세요"(`:200-203`)라고 안내한다 — 다시 만들어도 지난달 매니페스트는 생기지 않는다(매니페스트는 24일~말일 창에서만 생성).
- 코호트 병원이라도 그 달 매니페스트가 없으면(24일 창에서 `NO_MEASUREMENT_MANIFEST`) `_monthly_sov_measurement_succeeded`가
  False(`tasks.py:11198-11208`)라 보고서가 영원히 안 만들어지고, 로더는 `MISSING recoverable=True`로 "자동 재측정"을 약속한다(거짓 약속).
- 결론: **그 달 매니페스트가 없으면** 코호트 여부·보고서 존재와 무관하게 `MANIFEST_MISSING`이다. 매니페스트가 있으면 게이트 코드로 가른다.
  게이트의 원장 PDF 문제(`doctor_artifact_*`, `report_blocked`)는 `monthly_artifact_reconciliation`이 소유하므로 이 요약에서 뺀다(중복 알림 방지).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_monthly_report_gap_notifications.py` 끝에 추가

```python
from types import SimpleNamespace

from app.services.monthly_report_delivery import DeliveryGate


def _gate(monkeypatch, gate):
    monkeypatch.setattr(notifications, "monthly_report_delivery_gate", lambda *_args: gate)


def test_no_manifest_for_the_period_is_manifest_missing_even_with_a_final_report(monkeypatch):
    _gate(monkeypatch, DeliveryGate(False, "coverage_incomplete", "x"))
    report = SimpleNamespace(manifest_id=None)

    assert notifications.classify_monthly_report_gap(report, None, None) == "MANIFEST_MISSING"
    # 보고서가 아직 없어도 매니페스트가 없으면 자동 경로가 없다.
    assert notifications.classify_monthly_report_gap(None, None, None) == "MANIFEST_MISSING"


def test_gate_decides_the_remaining_states(monkeypatch):
    manifest = SimpleNamespace(id="m")
    assert notifications.classify_monthly_report_gap(None, manifest, None) == "MISSING"

    _gate(monkeypatch, DeliveryGate(True, None, None))
    assert notifications.classify_monthly_report_gap(SimpleNamespace(), manifest, None) is None

    for code in ("coverage_incomplete", "manifest_open", "manifest_mismatch"):
        _gate(monkeypatch, DeliveryGate(False, code, "x"))
        assert (
            notifications.classify_monthly_report_gap(SimpleNamespace(), manifest, None)
            == "COVERAGE_INCOMPLETE"
        )

    # 원장 PDF 문제는 원장 PDF 대조 작업의 몫이다 — 이 요약에서 중복으로 알리지 않는다.
    for code in ("doctor_artifact_missing", "doctor_artifact_invalid", "report_blocked"):
        _gate(monkeypatch, DeliveryGate(False, code, "x"))
        assert notifications.classify_monthly_report_gap(SimpleNamespace(), manifest, None) is None


def test_manifest_missing_is_one_honest_notice_per_hospital_and_never_promises_rebuild():
    gap = notifications.MonthlyReportGap("가의원", "MANIFEST_MISSING")

    intents = notifications.monthly_report_gap_intents(period_key="2026-09", gaps=[gap], day=1)

    [intent] = intents
    assert intent.notification_type == "MONTHLY_REPORT_GAP_SUMMARY"
    assert ":manifest_missing:" in intent.dedupe_key
    text = intent.message.fallback_text
    assert "전달 불가" in text
    assert "리포트 다시 만들기" not in intent.message.payload_json()
    assert "자동 재측정·마감을 진행합니다" not in text
    # 날짜가 바뀌어도 같은 병원·같은 달이면 같은 키다.
    again = notifications.monthly_report_gap_intents(period_key="2026-09", gaps=[gap], day=5)
    assert again[0].dedupe_key == intent.dedupe_key
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_report_gap_notifications.py -v`
  Expected: `AttributeError: module ... has no attribute 'classify_monthly_report_gap'`, 세 번째 테스트는 `MANIFEST_MISSING`이
  수동 COVERAGE 안내("리포트 다시 만들기")로 나가 FAIL.

- [ ] **Step 3: 최소 구현** — `backend/app/services/monthly_report_gap_notifications.py`

import 교체/추가:

```python
from app.models.monthly_control import MonthlyMeasurementManifest, MonthlyReportArtifact
from app.services.monthly_report_delivery import monthly_report_delivery_gate
```
(기존 `from app.services.monthly_report_delivery import coverage_is_final`는 더 쓰지 않으므로 지운다.)

상수와 분류 함수(`_MAX_NAMES` 아래):

```python
MANIFEST_MISSING = "MANIFEST_MISSING"
# 매니페스트는 있는데 측정이 끝나지 않았거나 다른 달·다른 병원 것에 묶인 상태. 자동 재측정·재생성이 푼다.
_COVERAGE_GATE_CODES = frozenset({"coverage_incomplete", "manifest_open", "manifest_mismatch"})


def classify_monthly_report_gap(report, period_manifest, artifact) -> str | None:
    """전달 게이트와 같은 사실로 공백을 가른다. None이면 이 요약이 알릴 공백이 아니다.

    그 달 매니페스트가 없으면 어떤 자동 경로도 지난달 측정을 만들지 못한다(매니페스트는 24일~말일
    창에서만 생긴다). 원장 PDF 문제는 월간 원장 PDF 대조 작업이 소유한다.
    """
    if period_manifest is None:
        return MANIFEST_MISSING
    if report is None:
        return "MISSING"
    gate = monthly_report_delivery_gate(report, period_manifest, artifact)
    if gate.ready:
        return None
    return "COVERAGE_INCOMPLETE" if gate.code in _COVERAGE_GATE_CODES else None
```

`load_monthly_report_gaps`의 병원 루프(`:69-96`)를 교체:

```python
    gaps: list[MonthlyReportGap] = []
    for hospital in hospitals:
        report = db.scalars(
            select(MonthlyReport)
            .where(
                MonthlyReport.hospital_id == hospital.id,
                MonthlyReport.period_year == period.year,
                MonthlyReport.period_month == period.month,
                MonthlyReport.report_type == "MONTHLY",
            )
            .order_by(MonthlyReport.version.desc())
            .limit(1)
        ).first()
        manifest = db.scalars(
            select(MonthlyMeasurementManifest).where(
                MonthlyMeasurementManifest.hospital_id == hospital.id,
                MonthlyMeasurementManifest.period_year == period.year,
                MonthlyMeasurementManifest.period_month == period.month,
            )
        ).first()
        artifact = (
            db.scalars(
                select(MonthlyReportArtifact).where(
                    MonthlyReportArtifact.report_id == report.id,
                    MonthlyReportArtifact.audience == "DOCTOR",
                )
            ).first()
            if report is not None
            else None
        )
        state = classify_monthly_report_gap(report, manifest, artifact)
        if state is None:
            continue
        recoverable = state == "MISSING" or (
            state == "COVERAGE_INCOMPLETE"
            and hospital_requires_monthly_sov_success(db, hospital, period_key)
        )
        gaps.append(MonthlyReportGap(hospital.name, state, recoverable=recoverable))
    return period_key, gaps
```
(docstring 첫 줄은 `"""Return MISSING, COVERAGE_INCOMPLETE and MANIFEST_MISSING facts for the prior month."""`로.)

새 빌더(`build_monthly_report_gap_summary` 아래):

```python
def build_monthly_report_manifest_missing_notice(
    *, period_key: str, gap: MonthlyReportGap
) -> NotificationIntent:
    """그 달 고정 측정이 없어 전달 가능한 리포트가 생길 수 없다 — 병원·월마다 한 건, 다시 만들기를 권하지 않는다."""

    name = safe_text(gap.hospital_name, 60)
    message = _message(
        event=MONTHLY_REPORT_GAP_SUMMARY_TYPE,
        header=f"[전달 불가] {period_key} {name} 월간 리포트",
        summary="월간 고정 측정 없음",
        counts_text=f"*{period_key}* · {name}\n월간 고정 측정(24일~말일) 대상이 아니었습니다.",
        detail=(
            f"대상: {name}\n"
            "이 달은 월간 고정 측정이 없어 원장님께 전달할 수 있는 리포트를 만들 수 없습니다. "
            "리포트를 다시 만들어도 바뀌지 않습니다. 이번 달은 원장님께 측정이 없었다는 사실을 안내해 주세요. "
            "다음 달은 측정 창(24일) 전에 자동으로 대상에 들어가며, 들어가지 못하면 측정 대상 공백 알림으로 따로 안내합니다."
        ),
        fallback_tail="이번 달 전달 불가 — 원장님께 안내해 주세요.",
    )
    return NotificationIntent(
        dedupe_key=(
            f"{MONTHLY_REPORT_GAP_SUMMARY_TYPE}:{period_key}:manifest_missing:{_digest(gap.hospital_name)}"
        ),
        notification_type=MONTHLY_REPORT_GAP_SUMMARY_TYPE,
        message=message,
    )
```

`monthly_report_gap_intents`(`:217-235`)의 분류 부분:

```python
    recovery_ahead = day <= MONTHLY_RECOVERY_END_DAY
    auto = [gap for gap in gaps if gap.recoverable and recovery_ahead]
    closing = [gap for gap in gaps if gap.recoverable and not recovery_ahead]
    manifest_missing = [gap for gap in gaps if gap.state == MANIFEST_MISSING]
    manual = [gap for gap in gaps if not gap.recoverable and gap.state != MANIFEST_MISSING]
    intents: list[NotificationIntent] = []
    if auto:
        intents.append(build_monthly_report_gap_auto_summary(period_key=period_key, gaps=auto))
    intents.extend(
        build_monthly_report_gap_summary(period_key=period_key, gaps=[gap]) for gap in manual
    )
    intents.extend(
        build_monthly_report_manifest_missing_notice(period_key=period_key, gap=gap)
        for gap in manifest_missing
    )
    if closing:
        ...  # 그대로
```

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_report_gap_notifications.py tests/test_operator_channel_signal.py tests/test_notification_labels.py -q`
  Expected: 전부 PASS. 추가로 `grep -n "coverage_is_final" app/services/monthly_report_gap_notifications.py` → 출력 없음.

- [ ] **Step 5: 커밋**
  `git add backend/app/services/monthly_report_gap_notifications.py backend/tests/test_monthly_report_gap_notifications.py && git commit -m "fix(monthly-gap): 공백을 전달 게이트로 판정하고 매니페스트 없는 달은 '전달 불가'로 정직하게 안내한다"`

**계약 영향:** CLAUDE.md "측정·리포트"의 코호트 문장 끝("이 공백을 조용히 두지 않는다") 뒤에 추가 — "그 달 고정 매니페스트가
없는 병원의 월간 공백은 `MANIFEST_MISSING`(전달 불가)으로 병원·월마다 한 번 알리고 다시 만들기를 권하지 않는다. 원장 PDF
문제는 이 요약이 아니라 원장 PDF 대조가 알린다."

**위험·롤백:** 지금까지 조용히 지나가던 "커버리지 최종이지만 매니페스트 없는" 병원이 배포 첫 1~8일에 처음 한 번씩 알려진다
(정상 동작, 운영자에게 미리 공지). 롤백은 revert.

---

### Task 1-6c: 코호트 비용 경고선을 비용 한도에서 계산하고, 초과 사고는 월이 끝나면 닫는다

**Files:**
- Modify: `backend/app/core/config.py:633-636` (필드 삭제)
- Modify: `backend/app/services/sov_tracking_set.py:1-18, 364-380` (함수 추가)
- Modify: `backend/app/workers/tasks.py:9990-10007` (`_warn_monthly_cohort_over_limit`)
- Modify: `backend/app/workers/monthly_cohort_incidents.py:64-100` (안내 문구)
- Modify: `backend/app/workers/incident_backlog.py:325-344` (해결 규칙)
- Modify: `.env.example:187`, `.env.production.example:145` (행 삭제)
- Test: `backend/tests/test_sov_monthly_redesign.py:321, 339, 706-720, 758`, `backend/tests/test_monthly_cohort_cost_threshold.py`(신규)

**조사 결과:**
- 경고선 `SOV_MONTHLY_COHORT_LIMIT=7`(`config.py:636`) < 병원 9곳 → 매 6시간 tick(24~말일)마다
  `open_monthly_cohort_over_limit`가 같은 사고를 touch(`tasks.py:9990-10007`, `monthly_cohort_incidents.py:64-100`)하고
  닫는 코드가 없다(`incident_backlog.py:333-344`에 없음).
- 실제 단가: 고정 세트 n=15(`run_monthly_sov_measurement`가 `n=15`로 등록, `tasks.py:10406`) × 플랫폼 2 × 반복 5 =
  **병원당 150호출/월** — `monthly_sov_guard_units(hospital_count, n)`가 이미 같은 식(`sov_tracking_set.py:364-378`:
  `hospital_count * n * 2 * 5`). 월 상한 `COST_GUARD_MONTHLY_SOV_QUERIES=4260`(`config.py:592`)이면 4260//150 = **28곳**.
- 일 상한 1260(`config.py:596`) < 9곳×150=1350이라 24일 하루에 다 못 끝내고 다음 tick·다음 날로 비용 보류가 이어진다
  (`MONTHLY_SOV_COST_GUARD_BLOCKED`, Task 1-6a로 RETRYING). 창이 최소 5일(2월 24~28)이라 1260×5/150=42곳 > 28곳 → **월 상한이 구속 조건**.
- 운영 env에 `SOV_MONTHLY_COHORT_LIMIT`이 명시돼 있어 기본값 변경으로는 안 고쳐진다(조사 요약 3).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_monthly_cohort_cost_threshold.py`

```python
"""코호트 비용 경고선은 비용 한도에서 계산한다 — 손으로 맞추는 병원 수 상수가 아니다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.config import Settings
from app.services import sov_tracking_set
from app.workers.incident_backlog import RESOLVERS


def test_threshold_is_monthly_budget_over_one_fixed_set(monkeypatch):
    monkeypatch.setattr(sov_tracking_set.settings, "COST_GUARD_MONTHLY_SOV_QUERIES", 4260)
    # 15문항 × 2플랫폼 × 5반복 = 150호출
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 28

    monkeypatch.setattr(sov_tracking_set.settings, "COST_GUARD_MONTHLY_SOV_QUERIES", 100)
    assert sov_tracking_set.monthly_sov_cohort_cost_threshold() == 1


def test_hand_tuned_cohort_limit_setting_is_gone():
    assert "SOV_MONTHLY_COHORT_LIMIT" not in Settings.model_fields


def _over_limit(source_id):
    return SimpleNamespace(source_id=source_id, hospital_id=None)


def test_over_limit_incident_closes_once_its_month_ended():
    resolver = RESOLVERS["MONTHLY_SOV_COHORT_OVER_LIMIT"]
    october_31 = datetime(2026, 10, 31, 14, 0, tzinfo=UTC)  # KST 11/1 23:00
    october_30 = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)  # KST 10/30 23:00

    assert resolver(None, _over_limit("2026-10"), october_31) == "cohort_month_ended"
    assert resolver(None, _over_limit("2026-10"), october_30) is None
    assert resolver(None, _over_limit("garbage"), october_31) is None
```

`backend/tests/test_sov_monthly_redesign.py` 수정:
- `:321` `monkeypatch.setattr(tasks.settings, "SOV_MONTHLY_COHORT_LIMIT", 2)` → `monkeypatch.setattr(tasks, "monthly_sov_cohort_cost_threshold", lambda: 2)`
- `:339` 같은 방식으로 `lambda: 1`
- `:710` `assert Settings.model_fields["SOV_MONTHLY_COHORT_LIMIT"].default == 7` 줄 삭제
- `:758` `monkeypatch.setattr(tasks.settings, "SOV_MONTHLY_COHORT_LIMIT", 7)` 줄 삭제

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_cohort_cost_threshold.py tests/test_sov_monthly_redesign.py -k "threshold or cohort_limit or over_limit or under_limit or guard_defaults or outside_month_end" -v`
  Expected: `AttributeError: ... 'monthly_sov_cohort_cost_threshold'`, `KeyError: 'MONTHLY_SOV_COHORT_OVER_LIMIT'`, 설정 필드 존재로 FAIL.

- [ ] **Step 3: 최소 구현**

`backend/app/services/sov_tracking_set.py` — import에 `from app.core.config import settings` 추가, `monthly_sov_guard_units` 아래:

```python
def monthly_sov_cohort_cost_threshold(n: int = TRACKING_SET_N_DEFAULT) -> int:
    """월간 측정 비용 경고선(병원 수) — 월 SoV 호출 상한을 병원 1곳의 고정 세트 비용으로 나눈 값.

    병원 1곳 = n문항 × 2플랫폼 × 5반복. 손으로 맞추는 병원 수 상수는 계약 병원이 늘 때마다
    매월 확정 경고를 만든다(2026-10: 상한 7 < 9곳).
    """

    return max(1, settings.COST_GUARD_MONTHLY_SOV_QUERIES // monthly_sov_guard_units(1, n))
```

`backend/app/core/config.py:633-636` — 주석 두 줄과 `SOV_MONTHLY_COHORT_LIMIT: int = 7`을 삭제하고 그 자리에:

```python
    # 월간 코호트는 계약된 ACTIVE 병원이 자동 편입되며 전원 측정한다. 비용 경고선은
    # `sov_tracking_set.monthly_sov_cohort_cost_threshold`가 COST_GUARD_MONTHLY_SOV_QUERIES에서 계산한다.
```

`backend/app/workers/tasks.py` — `from app.services.sov_tracking_set import (...)`(451-458)에 `monthly_sov_cohort_cost_threshold` 추가, `:9990-10007` 교체:

```python
def _warn_monthly_cohort_over_limit(cohort_size: int, period_key: str) -> None:
    """비용 경고선(월 SoV 상한 ÷ 병원 1곳 고정 세트 비용)을 넘으면 알리기만 한다. 병원을 자르지 않는다."""
    limit = monthly_sov_cohort_cost_threshold()
    if cohort_size <= limit:
        return
    logger.warning(
        "Monthly measurement cohort %d exceeds cost warning threshold %d; measuring all",
        cohort_size,
        limit,
    )
    try:
        _run_async(
            open_monthly_cohort_over_limit(
                period_key=period_key, cohort_size=cohort_size, limit=limit
            )
        )
    except Exception:
        logger.exception("Monthly cohort over-limit incident failed")
```

`backend/app/workers/monthly_cohort_incidents.py:85-89` next_action 교체:

```python
                next_action=(
                    "월 측정 비용 한도(COST_GUARD_MONTHLY_SOV_QUERIES)를 병원 수에 맞게 올릴지 비용 담당자와 "
                    "정하세요. 경고선은 한도에서 자동 계산되며 이 알림은 그 달이 끝나면 자동으로 닫힙니다."
                ),
```

`backend/app/workers/incident_backlog.py` — `_monthly_cohort_enrolled`(325-330) 아래:

```python
def _cohort_month_ended(_db: Session, incident: Incident, now: datetime) -> str | None:
    # 비용 경고는 그 달 측정에 관한 것이다. 달이 끝나면 손쓸 것이 없고, 다음 달에도 넘치면 새 달의 사고가 열린다.
    try:
        first = datetime.strptime(str(incident.source_id or ""), "%Y-%m").date()
    except ValueError:
        return None
    month_end = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return "cohort_month_ended" if now.astimezone(_KST).date() > month_end else None
```
`RESOLVERS`에 `"MONTHLY_SOV_COHORT_OVER_LIMIT": _cohort_month_ended,` 추가.

`.env.example:187`의 `SOV_MONTHLY_COHORT_LIMIT=9`, `.env.production.example:145`의 `SOV_MONTHLY_COHORT_LIMIT=7` 행 삭제.

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_cohort_cost_threshold.py tests/test_sov_monthly_redesign.py tests/test_sov_budget_and_sampling.py tests/test_cost_guard.py -q`
  Expected: 전부 PASS. `grep -rn "SOV_MONTHLY_COHORT_LIMIT" app tests ../.env*.example` → `sov_tracking_set.py` docstring 외 출력 없음(docstring의 옛 이름은 "예전에는 ..."라는 이력 문장이라 유지).

- [ ] **Step 5: 커밋**
  `git add backend/app/core/config.py backend/app/services/sov_tracking_set.py backend/app/workers/tasks.py backend/app/workers/monthly_cohort_incidents.py backend/app/workers/incident_backlog.py .env.example .env.production.example backend/tests/test_monthly_cohort_cost_threshold.py backend/tests/test_sov_monthly_redesign.py && git commit -m "fix(monthly-sov): 코호트 비용 경고선을 월 SoV 비용 한도에서 계산하고 초과 사고는 월말에 닫는다"`

**레지스트리 항목:** `MONTHLY_SOV_COHORT_OVER_LIMIT: initial_state=OPEN, resolver=_cohort_month_ended`.

**계약 영향:** CLAUDE.md "측정·리포트" 코호트 문장의 "`SOV_MONTHLY_COHORT_LIMIT`은 자르는 상한이 아니라 비용 경고 기준이다"를
"비용 경고선은 `COST_GUARD_MONTHLY_SOV_QUERIES ÷ (15×2×5)`로 계산한다(현재 28곳). 자르는 상한이 아니며 초과 사고는 그 달이 끝나면 닫힌다"로 교체.
배포 시 운영 env의 `SOV_MONTHLY_COHORT_LIMIT`은 무시되므로(`extra="ignore"`) `.env.production`과 운영 서비스 env에서 지운다(배포 기록에 남김).

**위험·롤백:** 경고선이 7→28로 올라 월 비용 경고가 사실상 꺼진다 — 대신 실제 지출 차단은 비용 가드(월 4260·일 1260)가 그대로
한다. 롤백은 revert(운영 env 값이 남아 있으면 즉시 예전 동작).

---

### Task 1-7b: 주간 실행에서 코호트 공백을 미리 알리고, 월말 창에서 기록 없는 병원을 1회 측정한다

**Files:**
- Modify: `backend/app/workers/tasks.py:9972-9988` (`_report_monthly_cohort_gaps`), `:10010-10055` (`run_weekly_monitoring`), `:10393-10422` (`run_monthly_sov_measurement`)
- Modify: `backend/app/workers/monthly_cohort_incidents.py:23-61` (+ recover 함수)
- Test: `backend/tests/test_monthly_cohort_bootstrap.py`(신규)

**조사 결과:**
- 코호트 공백 사고는 월말 창 tick에서만 열린다(`tasks.py:10410`). 주간 배치는 같은 등록 함수를 부르지만
  (`:10016-10018`) 공백을 보고하지 않아 24일 전엔 알 수 없다(감사 F6 맞음).
- 단 "기록 없음(no SovRecord)" 사유는 주간 배치가 **바로 이어서** 그 병원을 측정한다(코호트 밖 ACTIVE 전원 측정, `:10022-10050`).
  주간에서 이 사유로 사고를 열면 오탐이다. 주간에는 세트 부족(LOCAL·ACTIVE 10개 미만) 같은 사람 몫 사유만 연다.
- 월말 창에서 기록 없는 병원은 지금 OPEN·HIGH 공백 사고만 열리고 측정은 다음 월요일까지 없다. 같은 tick에서
  주간 측정 1회(`_ensure_weekly_sov_operation_run`, 병원·ISO주 멱등, `:10520-10577`)를 보내면 다음 6시간 tick이 편입한다.
- 편입되면 백로그 해결 규칙(`incident_backlog.py:325-330`)이 OPEN 공백만 닫는다(RETRYING은 안 본다) → 편입 사실을 받은
  쪽이 직접 닫는다.
- 비용: 부트스트랩 1회는 주간 경로 상한(`SOV_TOTAL_SPEC_CAP=50` spec × `SOV_REPEAT_COUNT_WEEKLY=5`, `config.py:639-649`)
  안이고 같은 `sov` 비용 카테고리를 쓴다. 24일엔 코호트(9×150)와 일 상한 1260을 나눠 쓰므로 **코호트 디스패치 뒤에** 보내
  같은 `sov` 큐에서 코호트가 먼저 돈다. 남는 슬롯은 비용 보류로 다음 tick이 이어 간다(Task 1-6a로 RETRYING).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_monthly_cohort_bootstrap.py`

```python
"""코호트 공백은 미리 알리고, 기록이 없어 못 들어온 병원은 기다리지 않고 1회 측정한다."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.models.hospital import HospitalStatus
from app.workers import tasks

NO_RECORD = {"name": "새의원", "reason": "no SovRecord", "hospital_id": str(uuid.uuid4())}
SHORT_SET = {
    "name": "질문부족의원",
    "reason": "not enough LOCAL ACTIVE targets: found 4, requires 10..15",
    "hospital_id": str(uuid.uuid4()),
}


def _capture_gaps(monkeypatch):
    opened = []
    monkeypatch.setattr(tasks, "open_monthly_cohort_gap", lambda **kw: opened.append(kw) or kw)
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)
    return opened


def test_weekly_reports_only_human_cohort_gaps(monkeypatch):
    opened = _capture_gaps(monkeypatch)

    tasks._report_monthly_cohort_gaps(
        {"not_enrolled": [NO_RECORD, SHORT_SET]},
        "2026-10",
        skip_reasons=frozenset({tasks.NO_SOV_RECORD_REASON}),
    )

    assert [item["hospital_name"] for item in opened] == ["질문부족의원"]
    assert opened[0]["retrying_until"] is None


def test_window_bootstrap_measures_unrecorded_hospital_and_gap_is_retrying(monkeypatch):
    hospital = SimpleNamespace(
        id=uuid.UUID(NO_RECORD["hospital_id"]), name="새의원", status=HospitalStatus.ACTIVE
    )
    db = SimpleNamespace(get=lambda _model, hospital_id: hospital if hospital_id == hospital.id else None)
    dispatched = []
    monkeypatch.setattr(
        tasks,
        "_dispatch_weekly_sov_measurement",
        lambda _db, h, week_key, _at: dispatched.append((h.id, week_key)) or True,
    )
    opened = _capture_gaps(monkeypatch)
    observed = tasks.datetime(2026, 10, 24, 0, 0, tzinfo=tasks.timezone.utc)

    bootstrapped = tasks._bootstrap_unmeasured_hospitals(
        db, {"not_enrolled": [NO_RECORD, SHORT_SET]}, tasks.date(2026, 10, 24), observed
    )
    tasks._report_monthly_cohort_gaps(
        {"not_enrolled": [NO_RECORD, SHORT_SET]},
        "2026-10",
        retrying_ids=bootstrapped,
        observed_at=observed,
    )

    assert dispatched == [(hospital.id, "2026-W43")]
    by_name = {item["hospital_name"]: item for item in opened}
    assert by_name["새의원"]["retrying_until"] == observed + tasks.COHORT_BOOTSTRAP_GRACE
    assert by_name["질문부족의원"]["retrying_until"] is None


def test_enrollment_closes_the_period_gap(monkeypatch):
    closed = []
    monkeypatch.setattr(tasks, "recover_monthly_cohort_gap", lambda **kw: closed.append(kw) or kw)
    monkeypatch.setattr(tasks, "_run_async", lambda value: value)
    enrolled_id = uuid.uuid4()

    tasks._recover_enrolled_cohort_gaps(
        {"enrolled": [{"hospital_id": str(enrolled_id), "name": "편입의원"}]}, "2026-10"
    )

    assert closed == [{"hospital_id": enrolled_id, "period_key": "2026-10"}]
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_cohort_bootstrap.py -v`
  Expected: `TypeError: _report_monthly_cohort_gaps() got an unexpected keyword argument 'skip_reasons'`, `AttributeError: ... 'NO_SOV_RECORD_REASON'` 등으로 3건 FAIL.

- [ ] **Step 3: 최소 구현**

`backend/app/workers/monthly_cohort_incidents.py`:
- import에 `from datetime import UTC, datetime`(이미 있음), `from sqlalchemy import select`, `from app.models.operations import Incident, IncidentSeverity, IncidentState`,
  `from app.services.incidents import build_incident_key, mark_recovered, mark_retrying, open_or_touch_incident` 추가.
- `open_monthly_cohort_gap` 시그니처에 `retrying_until: datetime | None = None` 추가, `incident = await open_or_touch_incident(...)` 다음에:

```python
        if retrying_until is not None:
            # 기록이 없어 못 들어온 병원에 측정을 방금 보냈다. 다음 tick이 편입하면 닫히고,
            # 기한 안에 편입되지 못하면 그때 사람의 일이다.
            retrying = await mark_retrying(
                db,
                incident.id,
                expected_version=incident.version,
                actor="monthly-sov-worker",
                reason="bootstrap measurement dispatched for cohort enrollment",
            )
            if isinstance(retrying, Incident):
                retrying.sla_due_at = retrying_until
```
  그리고 `severity=IncidentSeverity.HIGH`를 `severity=IncidentSeverity.MEDIUM if retrying_until is not None else IncidentSeverity.HIGH`로.
- 파일 끝에 추가:

```python
async def recover_monthly_cohort_gap(*, hospital_id: uuid.UUID, period_key: str) -> bool:
    """편입된 병원의 그 기간 공백 사고를 닫는다(OPEN·RETRYING 모두). 근거는 방금 커밋된 편입이다."""

    key = build_incident_key(
        "monthly_sov_cohort",
        "hospital_month",
        f"{hospital_id}:{period_key}",
        IncidentFingerprint.MISSING_PREREQUISITE,
    )
    sessions = get_async_sessionmaker()
    async with sessions() as db:
        incident = await db.scalar(
            select(Incident).where(
                Incident.dedupe_key == key,
                Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
            )
        )
        if incident is None:
            return False
        if incident.state == IncidentState.OPEN.value:
            retrying = await mark_retrying(
                db, incident.id, expected_version=incident.version,
                actor="monthly-sov-worker", reason="hospital enrolled in monthly cohort",
            )
            if not isinstance(retrying, Incident):
                await db.rollback()
                return False
            incident = retrying
        recovered = await mark_recovered(
            db, incident.id, expected_version=incident.version, observed_success=True,
            actor="monthly-sov-worker", reason="hospital enrolled in monthly cohort",
        )
        await db.commit()
        return isinstance(recovered, Incident)
```

`backend/app/workers/tasks.py`:
- `from app.workers.monthly_cohort_incidents import ...`에 `recover_monthly_cohort_gap` 추가
  (`grep -n "open_monthly_cohort_gap" app/workers/tasks.py | head -2`로 import 위치 확인).
- `_commit_new_cohort_enrollments` 위에 상수:

```python
NO_SOV_RECORD_REASON = "no SovRecord"
# 월말 창에서 기록 없는 병원에 1회 측정을 보낸 뒤 편입을 기다리는 시간. 6시간 tick 4번이다.
COHORT_BOOTSTRAP_GRACE = timedelta(hours=24)
```
- `_report_monthly_cohort_gaps`(9972-9988) 교체:

```python
def _report_monthly_cohort_gaps(
    registration: Any,
    period_key: str,
    *,
    skip_reasons: frozenset[str] = frozenset(),
    retrying_ids: frozenset[uuid.UUID] | set[uuid.UUID] = frozenset(),
    observed_at: datetime | None = None,
) -> None:
    """코호트 밖 ACTIVE 병원마다 기간별 사고 한 건. 실패해도 측정은 계속한다.

    방금 1회 측정을 보낸 병원(`retrying_ids`)은 다음 tick이 편입하므로 기한 있는 자동 복구로 연다.
    """
    if not isinstance(registration, Mapping):
        return
    at = observed_at or datetime.now(timezone.utc)
    for item in registration.get("not_enrolled") or []:
        if str(item.get("reason") or "") in skip_reasons:
            continue
        try:
            hospital_id = uuid.UUID(str(item["hospital_id"]))
            _run_async(
                open_monthly_cohort_gap(
                    hospital_id=hospital_id,
                    hospital_name=str(item.get("name") or "unknown"),
                    period_key=period_key,
                    reason=str(item.get("reason") or "unknown"),
                    retrying_until=(
                        at + COHORT_BOOTSTRAP_GRACE if hospital_id in retrying_ids else None
                    ),
                )
            )
        except Exception:
            logger.exception("Monthly cohort gap incident failed", extra={"item": str(item)})


def _recover_enrolled_cohort_gaps(registration: Any, period_key: str) -> None:
    """이번 등록으로 편입된 병원의 그 기간 공백 사고를 닫는다."""
    if not isinstance(registration, Mapping):
        return
    for item in registration.get("enrolled") or []:
        try:
            _run_async(
                recover_monthly_cohort_gap(
                    hospital_id=uuid.UUID(str(item["hospital_id"])), period_key=period_key
                )
            )
        except Exception:
            logger.exception("Monthly cohort gap recovery failed", extra={"item": str(item)})


def _dispatch_weekly_sov_measurement(
    db, hospital: Hospital, week_key: str, observed_at: datetime
) -> bool:
    """병원·ISO주 멱등 주간 측정 1회. 실행 기록이 생기면 True(발행 실패는 자율 복구가 다시 보낸다)."""
    run = _ensure_weekly_sov_operation_run(db, hospital, week_key, observed_at)
    if run is None or run.task_id is None:
        return False
    hospital_id = str(hospital.id)
    try:
        run_sov_for_hospital.apply_async(
            args=[hospital_id],
            queue="sov",
            headers={
                **build_dispatch_headers("run-sov", hospital_id),
                "operation_run_id": str(run.id),
            },
            task_id=run.task_id,
        )
    except Exception:
        logger.exception(
            "Weekly visibility measurement dispatch failed; autonomous recovery will redispatch",
            extra={"hospital_id": hospital_id, "operation_run_id": str(run.id)},
        )
        return True
    _mark_weekly_sov_operation_queued(db, run.id, observed_at)
    return True


def _bootstrap_unmeasured_hospitals(
    db, registration: Any, today_kst: date, observed_at: datetime
) -> set[uuid.UUID]:
    """기록이 없어 편입되지 못한 ACTIVE 병원에 주간 측정 1회를 바로 보낸다. 다음 6시간 tick이 편입한다."""
    dispatched: set[uuid.UUID] = set()
    if not isinstance(registration, Mapping):
        return dispatched
    week_key = _weekly_measurement_key(today_kst)
    for item in registration.get("not_enrolled") or []:
        if item.get("reason") != NO_SOV_RECORD_REASON:
            continue
        try:
            hospital = db.get(Hospital, uuid.UUID(str(item["hospital_id"])))
        except (KeyError, ValueError):
            continue
        if hospital is None or hospital.status != HospitalStatus.ACTIVE:
            continue
        if _dispatch_weekly_sov_measurement(db, hospital, week_key, observed_at):
            dispatched.add(hospital.id)
    return dispatched
```
  (`_weekly_measurement_key`·`_ensure_weekly_sov_operation_run`는 뒤쪽 `:10515,10520`에 정의돼 있지만 호출 시점에만 참조하므로 순서 무관.)
- `run_weekly_monitoring`(10010-10055): 등록 직후 두 줄 추가, 병원 루프 본문은 헬퍼로:

```python
        registration = register_convertible_tracking_sets(db, n=15, enroll_new=True)
        _commit_new_cohort_enrollments(db, registration)
        _log_blocked_convertible_tracking_sets(registration)
        month_key = f"{today_kst.year:04d}-{today_kst.month:02d}"
        _recover_enrolled_cohort_gaps(registration, month_key)
        # 기록 없는 병원은 이 실행이 바로 측정한다 — 사람 몫 사유만 24일 전에 미리 알린다.
        _report_monthly_cohort_gaps(
            registration, month_key, skip_reasons=frozenset({NO_SOV_RECORD_REASON})
        )
        ...
        for h in hospitals:
            _dispatch_weekly_sov_measurement(db, h, week_key, observed_at)
```
- `run_monthly_sov_measurement`(10405-10422) 본문:

```python
        registration = register_convertible_tracking_sets(db, n=15, enroll_new=True)
        _commit_new_cohort_enrollments(db, registration)
        _log_blocked_convertible_tracking_sets(registration)
        _recover_enrolled_cohort_gaps(registration, period_key)
        hospitals = _round_robin_hospitals(iter_monthly_sov_cohort(db), None)
        _warn_monthly_cohort_over_limit(len(hospitals), period_key)
        _dispatch_monthly_sov_period_sweep(
            db, hospitals, year=today_kst.year, month=today_kst.month,
            observed_at=observed_at, owner=owner,
        )
        # 코호트 측정을 먼저 큐에 넣은 뒤 보낸다 — 같은 sov 큐에서 코호트가 먼저 돈다.
        bootstrapped = _bootstrap_unmeasured_hospitals(db, registration, today_kst, observed_at)
        _report_monthly_cohort_gaps(
            registration, period_key, retrying_ids=bootstrapped, observed_at=observed_at
        )
```

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_monthly_cohort_bootstrap.py tests/test_sov_monthly_redesign.py tests/test_sov_repeat_alert_cut.py -q`
  Expected: 전부 PASS. 기존 `test_measurement_beats_log_each_blocked_tracking_set`·`test_weekly_*`가 `_dispatch_weekly_sov_measurement` 경유로도 통과하는지 확인한다
  (기존 테스트는 `_ensure_weekly_sov_operation_run`·`run_sov_for_hospital.apply_async`를 패치하므로 헬퍼 안에서 그대로 잡힌다). DB 통합:
  `.venv/bin/python -m pytest tests/integration/test_monthly_cohort_tracking_postgres.py -q` → PASS.

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/tasks.py backend/app/workers/monthly_cohort_incidents.py backend/tests/test_monthly_cohort_bootstrap.py && git commit -m "fix(monthly-sov): 코호트 공백을 주간 실행에서 미리 알리고 기록 없는 병원은 월말 창에서 1회 측정한다"`

**레지스트리 항목:** `MONTHLY_SOV_COHORT_GAP: initial_state=RETRYING if bootstrap dispatched else OPEN, deadline_fn=observed_at+24h, resolver=_monthly_cohort_enrolled(+편입 즉시 recover)`.

**계약 영향:** CLAUDE.md 코호트 문장의 "창이 열렸는데 편입되지 못한 ACTIVE 병원(기록 없음·세트 부족)은 병원·월마다 `MONTHLY_SOV_COHORT_GAP` 한 건으로
사람에게 알리고"를 "세트 부족은 주간 실행부터 병원·월마다 한 건으로 사람에게 알린다. 기록이 없는 병원은 월말 창 tick이 주간 측정 1회를 바로 보내고
그 공백을 24시간 자동 복구로 연다. 편입되면 그 기간 공백을 바로 닫는다"로 교체.

**위험·롤백:** 부트스트랩 측정이 24일 일 비용 상한을 일부 쓰므로 코호트 측정이 한 tick 늦어질 수 있다(Task 1-6a로 사람 큐에는 안 뜸).
롤백은 revert(스키마 변경 없음).

---

### Task 1-9a: V0_REPORT_FAILED에 6시간×3회 자동 재실행을 두고, 소진 뒤에만 OPEN으로 알린다

**Files:**
- Modify: `backend/app/services/ops_incident_alerts.py:36-117` (`retry_until`, `notify_if_retrying`)
- Modify: `backend/app/workers/v0_checkpoint.py:126-160` (lineage 창)
- Modify: `backend/app/workers/tasks.py:2805-2843` 근처(헬퍼 추가), `:4936-4940`(성공 후 복구), `:4997-5050`(최종 실패 분기)
- Modify: `backend/app/services/notification_copy.py:24-125` (해당 없음 — V0는 기존 문구 사용)
- Test: `backend/tests/test_v0_measurement_checkpoint.py`(추가), `backend/tests/integration/test_v0_auto_rerun_postgres.py`(신규), `backend/tests/test_v0_auto_rerun.py`(신규)

**조사 결과:**
- `trigger_v0_report`는 `max_retries=2`(`tasks.py:4475-4481`), 일반 예외는 120초 간격 2회 재시도 뒤 `V0_REPORT_FAILED`(OPEN·HIGH·Slack,
  `open_ops_incident` 기본 notify) 후 `raise`(`:4997-5038`). 자율 복구는 V0에 대해 **유실된 배포·만료 lease만** 다시 보낸다
  (`autonomous_recovery.py:76-78,143-166`) — FAILED 실행은 대상이 아니다.
- 안전한 재실행의 열쇠는 **같은 OperationRun id**다. 측정 lineage는 `MeasurementRun.config.operation_run_id`로 묶이고
  (`v0_checkpoint.py:76-87`), 미완료는 7일 안 같은 lineage를 이어 가며(`:174-205`, 슬롯 단위 체크포인트라 성공한 공급자 단계를 다시 사지 않음),
  완료 측정은 6시간 안에서만 재사용된다(`:138-160`) → 조사 요약 5에 따라 lineage 일치 시 7일로 넓힌다.
- 같은 id로 다시 보내는 기존 장치: `defer_operation_run` 패턴(QUEUED+`not_before_at`, `generation_run_control.py:393-424`)과
  자율 복구의 `not_before_at` 재배달(`autonomous_recovery.py:150-154,224-229`). V0 비용 보류가 이미 이 경로를 쓴다(`tasks.py:2805-2843`).
  미룬 실행은 성공 신호가 끝내지 못하고(판 증가) `record_task_success`도 `_deferred`면 손대지 않는다(`task_incident_control.py:144-148`).
- 재실행된 태스크는 측정 실행에 저장된 `workflow_failure_count`(최대 2, `tasks.py:2850-2860`)를 이어받아 **재실행 1회 = 시도 1회**가 된다(의도대로).
- 이중 사고(조사 요약 6): 최종 실패 전에 `finish_explicit_run(..., safe_error_code="V0_REPORT_FAILED")`로 실행을 끝내면 Celery 신호가 no-op이고
  (`operation_run_signals.py:249-255` RUNNING 조건), `terminal_outcome_identity`가 None(`operation_terminal_outcomes.py:79-81`)이 된다.
- V0 성공은 백로그 규칙(`_v0_report_created`, `incident_backlog.py:122-133`)이 **OPEN만** 닫는다 → RETRYING 사고는 성공 경로에서 직접 닫는다(`recover_ops_incident`, `ops_incident_alerts.py:120-174`).
- Admin "다시 측정"은 진행 중 V0 실행이 있으면 409다(`v0_claim.py:53-62`) — 재실행 대기(QUEUED) 동안도 409(비용 보류와 같은 동작).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_v0_measurement_checkpoint.py` 끝에 추가:

```python
def test_the_same_lineage_reuses_a_completed_measurement_for_seven_days():
    """6시간 간격 자동 재실행이 이미 끝난 150건 측정을 다시 사지 않는다."""
    hospital_id = uuid.uuid4()
    op = uuid.uuid4()
    db = _LookupSession([], {}, 0)
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    v0_checkpoint.find_reusable_v0_measurement_run(db, hospital_id, operation_run_id=op, now=now)

    cutoff = now - timedelta(seconds=v0_checkpoint.V0_RESUME_MAX_AGE_SECONDS)
    assert cutoff.strftime("%Y-%m-%d %H:%M:%S") in db.statements[0]
```

`backend/tests/test_v0_auto_rerun.py`:

```python
"""V0 최종 실패는 같은 실행으로 6시간 뒤 다시 돈다 — 3회 소진 뒤에만 사람에게 알린다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.workers import tasks

NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _patch(monkeypatch, *, rerun_at):
    opened, finished = [], []

    async def fake_open(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(tasks, "open_ops_incident", fake_open)
    monkeypatch.setattr(tasks, "_schedule_v0_auto_rerun", lambda _task, now=None: rerun_at)
    monkeypatch.setattr(tasks, "_finish_v0_run_failed", lambda _task, _hid: finished.append(True))
    return opened, finished


def test_a_retryable_final_failure_schedules_a_rerun_and_opens_retrying(monkeypatch):
    rerun_at = NOW + tasks.V0_AUTO_RERUN_INTERVAL
    opened, finished = _patch(monkeypatch, rerun_at=rerun_at)

    outcome = tasks._handle_v0_final_failure(
        SimpleNamespace(), str(uuid.uuid4()), RuntimeError("pdf render failed")
    )

    assert outcome == {"status": "auto_rerun_scheduled", "not_before": rerun_at.isoformat()}
    assert opened[0]["retry_until"] == rerun_at + tasks.V0_AUTO_RERUN_GRACE
    assert finished == []


def test_an_exhausted_budget_opens_and_terminalizes_once(monkeypatch):
    opened, finished = _patch(monkeypatch, rerun_at=None)

    outcome = tasks._handle_v0_final_failure(
        SimpleNamespace(), str(uuid.uuid4()), RuntimeError("pdf render failed")
    )

    assert outcome is None
    assert opened[0]["retry_until"] is None
    assert opened[0]["notify_if_retrying"] is True
    assert finished == [True]


def test_policy_drift_is_never_rerun(monkeypatch):
    opened, finished = _patch(monkeypatch, rerun_at=NOW + timedelta(hours=6))
    monkeypatch.setattr(
        tasks, "_schedule_v0_auto_rerun", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError)
    )

    outcome = tasks._handle_v0_final_failure(
        SimpleNamespace(), str(uuid.uuid4()), tasks.V0MeasurementPolicyDrift("drift")
    )

    assert outcome is None
    assert opened[0]["safe_error_code"] == "V0_MEASUREMENT_POLICY_DRIFT"
    assert finished == [True]


def test_rerun_budget_reads_fail_closed():
    assert tasks._v0_auto_reruns_used({}) == 0
    assert tasks._v0_auto_reruns_used({"v0_auto_reruns": 2}) == 2
    assert tasks._v0_auto_reruns_used({"v0_auto_reruns": "2"}) == tasks.V0_AUTO_RERUN_BUDGET
    assert tasks._v0_auto_reruns_used(None) == 0
```

`backend/tests/integration/test_v0_auto_rerun_postgres.py` (템플릿: `tests/integration/test_operation_run_deferral_postgres.py:1-130`):

```python
"""V0 자동 재실행은 같은 OperationRun을 미뤄 lineage를 지킨다(2026-10 감사 1-9a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import OperationRun, OperationRunState
from app.services.operation_run_payloads import DispatchPayload, build_request_payload
from app.workers import autonomous_recovery, tasks

NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _session(pg_conn) -> Session:
    return Session(bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def db(pg_conn):
    session = _session(pg_conn)
    try:
        yield session
    finally:
        session.close()


def _claimed_v0_run(db, *, reruns: int | None = None) -> OperationRun:
    hospital = Hospital(
        name="재실행 가상의원", slug=f"rerun-{uuid.uuid4().hex[:10]}", status=HospitalStatus.ACTIVE
    )
    db.add(hospital)
    db.flush()
    run = OperationRun(
        hospital_id=hospital.id,
        operation_type="TRIGGER_V0_REPORT",
        state=OperationRunState.RUNNING,
        task_id="v0-worker",
        lease_owner="v0-worker",
        lease_expires_at=NOW + timedelta(hours=1),
        version=3,
        attempt_count=1,
        request_payload=build_request_payload(
            DispatchPayload("hospital", str(hospital.id), "reports", (str(hospital.id),))
        ),
        result_summary={} if reruns is None else {"v0_auto_reruns": reruns},
        requested_at=NOW - timedelta(hours=1),
        queued_at=NOW - timedelta(hours=1),
    )
    db.add(run)
    db.commit()
    return run


def _worker(run, *, version: int = 3):
    return SimpleNamespace(
        request=SimpleNamespace(
            id=run.task_id,
            headers={"operation_run_id": str(run.id)},
            operation_run_claim_version=version,
        )
    )


def test_first_final_failure_defers_the_same_run_six_hours(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))

    rerun_at = tasks._schedule_v0_auto_rerun(_worker(run), now=NOW)

    assert rerun_at == NOW + timedelta(hours=6)
    db.expire_all()
    stored = db.get(OperationRun, run.id)
    assert stored.state == OperationRunState.QUEUED
    assert stored.not_before_at == rerun_at
    assert stored.result_summary["v0_auto_reruns"] == 1
    assert stored.lease_owner is None


def test_an_exhausted_budget_does_not_defer(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db, reruns=3)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))

    assert tasks._schedule_v0_auto_rerun(_worker(run), now=NOW) is None
    db.expire_all()
    assert db.get(OperationRun, run.id).state == OperationRunState.RUNNING


def test_a_stale_copy_cannot_schedule_a_rerun(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))

    assert tasks._schedule_v0_auto_rerun(_worker(run, version=2), now=NOW) is None


def test_the_rerun_is_sent_once_after_its_time_with_the_same_lineage(db, pg_conn, monkeypatch):
    run = _claimed_v0_run(db)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: _session(pg_conn))
    rerun_at = tasks._schedule_v0_auto_rerun(_worker(run), now=NOW)
    sent: list[dict] = []
    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", lambda: _session(pg_conn))
    monkeypatch.setattr(
        autonomous_recovery.celery_app,
        "send_task",
        lambda name, args, **kwargs: sent.append({"name": name, "args": args, **kwargs}),
    )

    monkeypatch.setattr(autonomous_recovery, "_now", lambda: rerun_at - timedelta(minutes=1))
    autonomous_recovery.reconcile.run()
    assert [c for c in sent if c["headers"].get("operation_run_id") == str(run.id)] == []

    monkeypatch.setattr(autonomous_recovery, "_now", lambda: rerun_at + timedelta(minutes=1))
    autonomous_recovery.reconcile.run()
    mine = [c for c in sent if c["headers"].get("operation_run_id") == str(run.id)]
    assert len(mine) == 1
    assert mine[0]["name"] == "app.workers.tasks.trigger_v0_report"
    db.expire_all()
    assert db.get(OperationRun, run.id).result_summary["v0_auto_reruns"] == 1
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_v0_auto_rerun.py tests/test_v0_measurement_checkpoint.py::test_the_same_lineage_reuses_a_completed_measurement_for_seven_days -v`
  Expected: `AttributeError: module 'app.workers.tasks' has no attribute 'V0_AUTO_RERUN_INTERVAL'` 등, 체크포인트 테스트는 6시간 cutoff 문자열이라 FAIL.
  통합: `.venv/bin/python -m pytest tests/integration/test_v0_auto_rerun_postgres.py -v` → `AttributeError: ... '_schedule_v0_auto_rerun'`.

- [ ] **Step 3: 최소 구현**

`backend/app/workers/v0_checkpoint.py:136-138`:

```python
    moment = now or datetime.now(UTC)
    # 같은 V0 요청(lineage)의 재실행은 지금의 진단을 이어 만드는 것이다 — 자동 재실행이 6시간 뒤에
    # 돌아도 이미 끝난 측정을 다시 사지 않는다. lineage 없는 옛 배포만 6시간 창을 쓴다.
    max_age = (
        V0_RESUME_MAX_AGE_SECONDS if operation_run_id is not None else V0_CHECKPOINT_MAX_AGE_SECONDS
    )
    cutoff = moment - timedelta(seconds=max_age)
```

`backend/app/services/ops_incident_alerts.py` `open_ops_incident`:
- 시그니처 끝에 `retry_until: datetime | None = None, notify_if_retrying: bool = False,` 추가.
- `incident = await open_or_touch_incident(...)` 다음, notify 판단 전에:

```python
        if retry_until is not None:
            # 기계가 기한 안에 다시 해 본다 — 사람의 일이 아니고 Slack도 보내지 않는다.
            retrying = await mark_retrying(
                db,
                incident.id,
                expected_version=incident.version,
                actor=actor,
                reason="automatic rerun owns recovery until its deadline",
            )
            if isinstance(retrying, Incident):
                incident = retrying
                incident.sla_due_at = retry_until
        notify_states = {IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value}
        if notify_if_retrying:
            notify_states.add(IncidentState.RETRYING.value)
        if notify and retry_until is None and (
            previous_state is None or previous_state in notify_states
        ):
```
(기존 `if notify and (previous_state is None or previous_state in {...}):`를 위 조건으로 교체, 본문은 그대로.)

`backend/app/workers/tasks.py` — `_defer_v0_until_cost_window` 아래(2843행 뒤)에 추가:

```python
V0_AUTO_RERUN_BUDGET = 3
V0_AUTO_RERUN_INTERVAL = timedelta(hours=6)
# 재실행이 실제로 돌 시간. 이 안에 끝나지 않으면 그때 사람의 일이다.
V0_AUTO_RERUN_GRACE = timedelta(hours=2)
V0_AUTO_RERUN_SUMMARY_KEY = "v0_auto_reruns"
V0_AUTO_RERUN_NEXT_ACTION = (
    "시스템이 같은 진단을 6시간 뒤 자동으로 다시 만듭니다(최대 3회). 이미 끝난 측정은 다시 사지 않습니다. "
    "조치하지 마세요 — 끝내 실패하면 그때 다시 알립니다."
)


def _v0_auto_reruns_used(summary: object) -> int:
    if not isinstance(summary, Mapping):
        return 0
    value = summary.get(V0_AUTO_RERUN_SUMMARY_KEY, 0)
    if type(value) is int and 0 <= value <= V0_AUTO_RERUN_BUDGET:
        return value
    # 손상된 예산으로 재실행을 무한히 열지 않는다.
    return V0_AUTO_RERUN_BUDGET


def _schedule_v0_auto_rerun(task, *, now: datetime | None = None) -> datetime | None:
    """같은 실행을 6시간 뒤로 미룬다. 예산 소진·기록 없음·지난 사본이면 None(=지금 사람의 일).

    같은 OperationRun id라 측정 lineage(v0_checkpoint)가 이어진다 — 성공한 공급자 단계는 다시 사지 않는다.
    """
    context = explicit_run_context(task)
    if context is None:
        return None
    observed = now or datetime.now(timezone.utc)
    not_before = observed + V0_AUTO_RERUN_INTERVAL
    try:
        with SyncSessionLocal() as db:
            run = db.get(OperationRun, context.run_id)
            if run is None:
                return None
            used = _v0_auto_reruns_used(run.result_summary)
            if used >= V0_AUTO_RERUN_BUDGET:
                return None
            summary = {**(run.result_summary or {}), V0_AUTO_RERUN_SUMMARY_KEY: used + 1}
            deferred = db.execute(
                update(OperationRun)
                .where(
                    OperationRun.id == context.run_id,
                    OperationRun.task_id == context.worker_id,
                    OperationRun.state == OperationRunState.RUNNING,
                    OperationRun.lease_owner == context.worker_id,
                    OperationRun.version == context.version,
                )
                .values(
                    state=OperationRunState.QUEUED,
                    queued_at=observed,
                    not_before_at=not_before,
                    heartbeat_at=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    result_summary=summary,
                    version=OperationRun.version + 1,
                )
                .returning(OperationRun.id)
            ).scalar_one_or_none()
            db.commit()
    except Exception:  # noqa: BLE001 - 미룸을 못 남기면 지금 사람에게 넘긴다(아래 OPEN).
        logger.exception("V0 auto rerun could not be scheduled")
        return None
    return not_before if deferred is not None else None


def _finish_v0_run_failed(task, hospital_id: str) -> None:
    """도메인 사고(V0_REPORT_FAILED)가 이미 열렸다 — 실행을 명시 종결해 일반 실패 사고가 겹치지 않게 한다."""
    try:
        with SyncSessionLocal() as db:
            finish_explicit_run(
                db,
                task,
                uuid.UUID(hospital_id),
                OperationRunState.FAILED,
                safe_error_code="V0_REPORT_FAILED",
                safe_error_message="초기 진단 리포트를 만들지 못했습니다.",
            )
    except Exception:  # noqa: BLE001 - 종결 실패는 Celery 신호가 TASK_FAILED로 대신 끝낸다.
        logger.exception("V0 run could not be terminalized explicitly")


def _handle_v0_final_failure(task, hospital_id: str, exc: BaseException) -> dict | None:
    """재시도가 끝난 V0 실패. 자동 재실행을 잡으면 그 결과를, 사람에게 넘기면 None을 돌려준다."""
    summary = getattr(exc, "summary", None)
    summary = summary if isinstance(summary, Mapping) else {}
    code = summary.get("safe_error_code", "V0_REPORT_RETRIES_EXHAUSTED")
    # 측정 기준 변경은 같은 lineage로 다시 돌려도 같은 이유로 멈춘다.
    rerun_at = (
        None if isinstance(exc, V0MeasurementPolicyDrift) else _schedule_v0_auto_rerun(task)
    )
    try:
        _run_async(
            open_ops_incident(
                pipeline="v0_report",
                object_type="hospital",
                object_id=hospital_id,
                incident_type="V0_REPORT_FAILED",
                safe_error_code=code,
                problem=summary.get(
                    "safe_error_message", "초기 진단 리포트 생성 재시도가 모두 실패했습니다."
                ),
                customer_impact="초기 진단 리포트를 준비할 수 없습니다.",
                next_action=(
                    V0_AUTO_RERUN_NEXT_ACTION
                    if rerun_at is not None
                    else summary.get(
                        "next_action",
                        "운영센터에서 원인을 확인하고 초기 진단 리포트를 수동 재실행하세요.",
                    )
                ),
                source_type="V0_REPORT",
                hospital_name="병원 초기 진단",
                hospital_id=uuid.UUID(hospital_id),
                actor="v0-report-worker",
                retry_until=(rerun_at + V0_AUTO_RERUN_GRACE) if rerun_at is not None else None,
                notify_if_retrying=True,
            )
        )
    except Exception:
        logger.exception("V0 final-failure ops alert delivery failed (non-fatal)")
    if rerun_at is not None:
        return {"status": "auto_rerun_scheduled", "not_before": rerun_at.isoformat()}
    _finish_v0_run_failed(task, hospital_id)
    return None
```

`trigger_v0_report` 최종 실패 분기(`tasks.py:4997-5038`)를 교체:

```python
        if terminal_measurement_failure or failure_retry_count >= self.max_retries:
            # 공급자별 재시도와 회로 차단까지 끝난 실패다. 같은 실행을 6시간 뒤로 미뤄 최대 3회 다시
            # 해 보고(측정 lineage 유지), 소진 뒤에만 사람에게 넘긴다.
            outcome = _handle_v0_final_failure(self, hospital_id, exc)
            if outcome is not None:
                return outcome
            raise exc
```

성공 경로 — `_seed_query_targets_from_matrix_sync(hospital.id)`(`tasks.py:4937`) 바로 아래:

```python
            # 자동 재실행 중(RETRYING)이던 실패 사고는 백로그 규칙이 보지 않는다 — 성공한 이 실행이 닫는다.
            try:
                _run_async(
                    recover_ops_incident(
                        pipeline="v0_report",
                        object_type="hospital",
                        object_id=hospital_id,
                        fingerprint=IncidentFingerprint.UNKNOWN,
                        hospital_name="병원 초기 진단",
                        actor="v0-report-worker",
                        reason="V0 report created",
                    )
                )
            except Exception:
                logger.exception("V0 failure incident recovery failed (non-fatal)")
```

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_v0_auto_rerun.py tests/test_v0_measurement_checkpoint.py tests/test_autonomous_loop_recovery.py tests/test_worker_dispatch_auth.py -q` → PASS.
  통합: `.venv/bin/python -m pytest tests/integration/test_v0_auto_rerun_postgres.py tests/integration/test_operation_run_deferral_postgres.py tests/integration/test_incident_backlog_postgres.py -q` → PASS.
  회귀: `.venv/bin/python -m pytest tests -q -k "v0 or ops_incident"` → PASS.

- [ ] **Step 5: 커밋**
  `git add backend/app/services/ops_incident_alerts.py backend/app/workers/v0_checkpoint.py backend/app/workers/tasks.py backend/tests/test_v0_auto_rerun.py backend/tests/test_v0_measurement_checkpoint.py backend/tests/integration/test_v0_auto_rerun_postgres.py && git commit -m "fix(v0): 최종 실패는 같은 실행으로 6시간×3회 자동 재실행하고 소진 뒤에만 알린다"`

**레지스트리 항목:** `V0_REPORT_FAILED: initial_state=RETRYING if rerun scheduled else OPEN, deadline_fn=not_before+2h, resolver=_v0_report_created(+성공 경로 recover)`.

**계약 영향:** CLAUDE.md "측정·리포트"의 V0 문장("V0는 짧은 실행 구간마다 같은 작업·측정 lineage로 자동 이어가고 …") 뒤에 추가 —
"재시도가 끝난 V0 실패는 같은 실행을 6시간 뒤로 미뤄 최대 3회 다시 돌린다(측정 기준 변경 제외). 같은 lineage는 완료 측정을 7일 안에서
재사용한다. 재실행 중 사고는 RETRYING이고 소진 뒤에만 OPEN·Slack이다."

**위험·롤백:** ①lineage 창 확대로 최대 7일 된 완료 측정이 진단 리포트에 쓰일 수 있다 — 같은 요청의 측정이라 의미상 맞지만 **대표 확인 필요**(아래 질문 2).
②재실행 대기 동안 Admin 수동 V0는 409. 롤백은 revert(스키마 변경 없음, `result_summary` 키만 추가).

---

### Task 1-9b: 리드 진단 실행 재시도를 즉시·1h·2h·4h로 물러서 5회 준다

**Files:**
- Modify: `backend/app/workers/lead_diagnosis_tasks.py:62-65, 218-252, 254-290`
- Test: `backend/tests/test_lead_diagnosis_retry_backoff.py`(신규), `backend/tests/integration/test_lead_diagnosis_drain.py`(추가)

**조사 결과:**
- `MAX_EXECUTION_ATTEMPTS = 3`(`lead_diagnosis_tasks.py:65`). 실패하면 즉시 PENDING으로 돌아가(`:233-252` 예외, `:262-289` 확정 FAILED 재큐)
  1분 drain이 바로 다시 집으므로 **3회가 약 3분 안에 소진**된다 — 10분짜리 공급자 장애도 사람 몫이 된다.
- PENDING 선택·claim은 이미 `cost_deferred_until <= now`를 존중한다(`:191-196`, `:743-747`). 이 열은 비용 보류용 이름이지만 Admin·API에서 읽지
  않고(`grep` 결과 엔진 `:537,547,690`과 워커만) 의미가 "다음 실행 가능 시각"이다 → 마이그레이션 없이 재시도 물러서기에도 쓴다.
  엔진은 `cost_defer_reason`이 있을 때만 이 값을 지운다(`lead_diagnosis_engine.py:689-693`) — 물러서기는 reason을 안 남기므로 충돌 없음.
- 재실행은 저장된 답변을 다시 사지 않는다("commit resumes from the stored answer", `lead_diagnosis_engine.py:500-516`) → 비용은 실패분만.
- 셀프 무료 진단은 Site에서 종료됐다(CLAUDE.md #151). 15분 SLA 주석(`:12-15`)은 도입문의 INTERNAL 진단에는 해당이 약하다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_lead_diagnosis_retry_backoff.py`:

```python
from datetime import UTC, datetime, timedelta

from app.workers import lead_diagnosis_tasks as leadgen

NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def test_first_failure_retries_now_then_backs_off_hourly():
    assert leadgen.MAX_EXECUTION_ATTEMPTS == 5
    assert leadgen.execution_retry_not_before(1, NOW) == NOW
    assert leadgen.execution_retry_not_before(2, NOW) == NOW + timedelta(hours=1)
    assert leadgen.execution_retry_not_before(3, NOW) == NOW + timedelta(hours=2)
    assert leadgen.execution_retry_not_before(4, NOW) == NOW + timedelta(hours=4)
    # 소진 뒤에는 시각이 없다 — drain이 FAILED로 종결하고 사고를 연다.
    assert leadgen.execution_retry_not_before(5, NOW) is None
```

`backend/tests/integration/test_lead_diagnosis_drain.py` 끝에 추가:

```python
@pytest.mark.asyncio
class TestRetryBackoff:
    async def test_a_backed_off_diagnosis_is_not_dispatched_until_its_time(self, pg_async_session):
        now = datetime.now(timezone.utc)
        diagnosis = await _seed(
            pg_async_session,
            execution_status=ExecutionStatus.RUNNING.value,
            execution_attempts=2,
            running_since=now,
        )

        await leadgen_tasks._requeue_with_backoff(
            pg_async_session, diagnosis.id, attempts=2, error="Timeout", now=now
        )

        await pg_async_session.refresh(diagnosis)
        assert diagnosis.execution_status == ExecutionStatus.PENDING.value
        assert diagnosis.cost_deferred_until == now + timedelta(hours=1)
        assert diagnosis.error == "Timeout"
        assert str(diagnosis.id) not in await leadgen_tasks._pending_to_dispatch(pg_async_session)
        assert await leagen_claim(pg_async_session, diagnosis.id) is False

    async def test_fourth_attempt_is_still_claimable(self, pg_async_session):
        diagnosis = await _seed(pg_async_session, execution_attempts=4)
        assert await leagen_claim(pg_async_session, diagnosis.id) is True
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_lead_diagnosis_retry_backoff.py -v` → `AssertionError: assert 3 == 5` / `AttributeError: ... execution_retry_not_before`.
  통합: `.venv/bin/python -m pytest tests/integration/test_lead_diagnosis_drain.py -k Backoff -v` → `AttributeError: ... '_requeue_with_backoff'`.

- [ ] **Step 3: 최소 구현** — `backend/app/workers/lead_diagnosis_tasks.py`

`:64-65`를 교체:

```python
# 실행 재시도 상한. 넘으면 FAILED로 종결하고 AE에게 넘긴다(= DLQ). 첫 실패는 바로(일시 장애),
# 이후 1·2·4시간을 물러서 공급자 장애가 몇 시간 이어져도 사람 몫이 되지 않게 한다.
MAX_EXECUTION_ATTEMPTS = 5
EXECUTION_RETRY_BACKOFF = (
    timedelta(0),
    timedelta(hours=1),
    timedelta(hours=2),
    timedelta(hours=4),
)


def execution_retry_not_before(attempts_done: int, now: datetime) -> datetime | None:
    """`attempts_done`번째 시도가 실패한 뒤 다음 시도가 가능한 시각. 소진이면 None."""
    if attempts_done >= MAX_EXECUTION_ATTEMPTS:
        return None
    index = min(max(attempts_done, 1), len(EXECUTION_RETRY_BACKOFF)) - 1
    return now + EXECUTION_RETRY_BACKOFF[index]


async def _requeue_with_backoff(
    session, diagnosis_id, *, attempts: int, error: str | None = None, now: datetime | None = None,
    from_failed: bool = False,
) -> None:
    """실패한 진단을 PENDING으로 되돌리되 물러설 시각을 남긴다(`cost_deferred_until` = 다음 실행 가능 시각)."""
    observed = now or datetime.now(timezone.utc)
    values = {
        "execution_status": ExecutionStatus.PENDING.value,
        "running_since": None,
        "cost_deferred_until": execution_retry_not_before(attempts, observed),
    }
    if error is not None:
        values["error"] = error
    predicates = [LeadDiagnosis.id == diagnosis_id]
    if from_failed:
        predicates.append(LeadDiagnosis.execution_status == ExecutionStatus.FAILED.value)
        values["finished_at"] = None
    await session.execute(update(LeadDiagnosis).where(*predicates).values(**values))
    await session.commit()
```

`_run_lead_diagnosis`: 진단 로드 직후(`:228` `return {"skipped": "missing"}` 다음 줄)에 `attempts = int(diagnosis.execution_attempts or 1)`를 잡고
(롤백 뒤 async lazy-load를 피한다), 예외 분기(`:233-252`)를 교체:

```python
        except Exception as exc:
            # 실행 중 죽으면 RUNNING으로 남는다. 되돌려 놓아야 폴러가 다시 집는다.
            await session.rollback()
            error = f"{type(exc).__name__}: {exc}"[:2000]
            if recovery_expected_attempts is not None:
                await session.execute(
                    update(LeadDiagnosis)
                    .where(LeadDiagnosis.id == pk)
                    .values(
                        execution_status=ExecutionStatus.FAILED.value,
                        running_since=None,
                        error=error,
                    )
                )
                await session.commit()
            else:
                await _requeue_with_backoff(session, pk, attempts=attempts, error=error)
            raise
```

확정 FAILED 재큐(`:262-289`)의 조건 `(diagnosis.execution_attempts or 1) < MAX_EXECUTION_ATTEMPTS`는 그대로 두고, 본문의 `session.execute(update(...))`+`commit`을
`await _requeue_with_backoff(session, pk, attempts=attempts, from_failed=True)`로 교체한다(로그 줄은 유지).

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_lead_diagnosis_retry_backoff.py -q` → PASS.
  통합: `.venv/bin/python -m pytest tests/integration/test_lead_diagnosis_drain.py tests/integration/test_lead_diagnosis_engine.py tests/integration/test_manual_lead_diagnosis.py tests/integration/test_lead_recovery_incident_backlog_postgres.py -q` → PASS.

- [ ] **Step 5: 커밋**
  `git add backend/app/workers/lead_diagnosis_tasks.py backend/tests/test_lead_diagnosis_retry_backoff.py backend/tests/integration/test_lead_diagnosis_drain.py && git commit -m "fix(lead-diagnosis): 측정 실패 재시도를 즉시·1·2·4시간으로 물러서 5회 준다"`

**계약 영향:** CLAUDE.md "도입문의" 문장 끝에 "진단 측정 실패는 즉시·1·2·4시간 물러서 최대 5회 다시 하고 소진 뒤에만 사람에게 넘긴다"를 덧붙인다.
`docs/ops/inquiry-intake-automation.md`의 재시도 설명도 같은 값으로.

**위험·롤백:** 최악의 경우 진단이 약 7시간 늦게 실패로 확정된다(셀프 무료 진단은 종료돼 고객 대기 SLA 영향 없음). 실패분 공급자 호출이 최대 2회 더 든다
(성공 답변은 재구매 없음). 롤백은 revert.

---

### Task 1-9c-1: 도입문의 Slack 실패는 PENDING으로 남기고 drain이 1시간 재시도한 뒤 사고로 올린다

**Files:**
- Modify: `backend/app/services/inquiry_diagnosis.py:33` 근처(문구 상수 이동)
- Modify: `backend/app/api/public/leads.py:18-21, 212-223`
- Modify: `backend/app/workers/lead_diagnosis_tasks.py:76-79, 99-158, 674-690, 699-756(drain)`
- Modify: `backend/app/workers/incident_backlog.py:325-344`, `backend/app/services/notification_copy.py:115-120`
- Test: `backend/tests/test_public_leads.py:89-106`, `backend/tests/integration/test_lead_intake_notification_retry.py`(신규), `backend/tests/test_lead_followup_resolvers.py`(신규)

**조사 결과:**
- 접수 API는 Slack 실패 시 `notification_status="FAILED"`로 끝낸다(`leads.py:222-223`). drain은 `source == AI_DIAGNOSIS`의 NULL/PENDING만 집고
  (`lead_diagnosis_tasks.py:676-690`), claim도 같은 조건(`:103-110`), 발송 함수는 무료 진단용 `notify_lead_diagnosis_received`다(`:128-145`).
  → 도입문의는 상태만 바꿔서는 재발송되지 않는다(조사 요약 8).
- 도입문의 행은 API가 Slack을 보내는 동안 상태가 NULL이다 → drain은 도입문의의 **명시적 PENDING만** 집는다(중복 방지).
- Slack 문구에 들어가는 "자동 처리" 한 줄(`leads.py:19-21`)은 drain에서 다시 만들 수 있다: 활성 진단이 있으면 QUEUED
  (`inquiry_diagnosis.active_diagnosis_for`, `inquiry_diagnosis.py:62`), 세 입력이 모두 있으면 REFUSED, 아니면 NO_INPUT(`leads.py:102-111`과 같은 규칙).
- 웹훅이 오래 죽어 있으면 매분 재시도가 계속되므로 1시간 창으로 끊고 `LEAD_INTAKE_NOTIFICATION_FAILED` 사고(outbox 경유 → 채널 생존 처리·대체 전송 계약을 탄다)로 넘긴다.
  사고는 AE가 리드를 처리하면(상태 ≠ NEW) 백로그가 닫는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_public_leads.py:89-106`의 `test_create_lead_records_notification_failure` 마지막 두 줄을 교체:

```python
    # 실패는 끝이 아니다 — drain이 1시간 동안 다시 보낸다.
    assert db.added[0].notification_status == "PENDING"
    assert "Slack/webhook" in db.added[0].notification_error
```

`backend/tests/test_lead_followup_resolvers.py`:

```python
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.workers.incident_backlog import RESOLVERS

NOW = datetime(2026, 10, 11, tzinfo=UTC)


@pytest.mark.parametrize("kind", ["LEAD_INTAKE_NOTIFICATION_FAILED", "INQUIRY_SMS_FAILED"])
@pytest.mark.parametrize(
    "lead,expected",
    [
        (SimpleNamespace(status="NEW", purged_at=None), None),
        (SimpleNamespace(status="CONTACTED", purged_at=None), "lead_followed_up"),
        (SimpleNamespace(status="NEW", purged_at=NOW), "lead_purged"),
        (None, "lead_purged"),
    ],
)
def test_lead_side_effect_incidents_close_once_a_person_took_the_lead(kind, lead, expected):
    db = SimpleNamespace(get=lambda _model, _id: lead)
    incident = SimpleNamespace(source_id=str(uuid.uuid4()), hospital_id=None)

    assert RESOLVERS[kind](db, incident, NOW) == expected
```

`backend/tests/integration/test_lead_intake_notification_retry.py`:

```python
"""도입문의 접수 Slack 실패는 drain이 다시 보낸다(2026-10 감사 F12)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.lead import SalesLead
from app.services import notifier
from app.services.inquiry_diagnosis import DIAGNOSIS_NOTE_NO_INPUT
from app.workers import lead_diagnosis_tasks as leadgen


async def _inquiry(session, *, status, created_at=None):
    lead = SalesLead(
        clinic_name="재발송의원", clinic_type="도입문의", contact="010-0000-0000",
        privacy=True, source="INQUIRY", notification_status=status,
    )
    session.add(lead)
    await session.flush()
    if created_at is not None:
        lead.created_at = created_at
        await session.flush()
    return lead


@pytest.mark.asyncio
async def test_drain_picks_only_explicit_pending_inquiries_within_the_window(pg_async_session):
    now = datetime.now(timezone.utc)
    pending = await _inquiry(pg_async_session, status="PENDING")
    in_flight = await _inquiry(pg_async_session, status=None)
    stale = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))

    picked = await leadgen._lead_notifications_to_dispatch(pg_async_session)

    assert str(pending.id) in picked
    assert str(in_flight.id) not in picked  # API가 아직 보내는 중일 수 있다
    assert str(stale.id) not in picked


@pytest.mark.asyncio
async def test_expired_pending_inquiry_becomes_failed(pg_async_session):
    now = datetime.now(timezone.utc)
    stale = await _inquiry(pg_async_session, status="PENDING", created_at=now - timedelta(hours=2))

    expired = await leadgen._expire_inquiry_notifications(pg_async_session, now=now)

    assert [lead.id for lead in expired] == [stale.id]
    await pg_async_session.refresh(stale)
    assert stale.notification_status == "FAILED"


@pytest.mark.asyncio
async def test_inquiry_resend_uses_the_inquiry_message_and_stays_pending_on_failure(
    pg_async_session, monkeypatch
):
    lead = await _inquiry(pg_async_session, status="PENDING")
    calls = []

    async def fake_created(**payload):
        calls.append(payload)
        return False

    monkeypatch.setattr(notifier, "notify_lead_created", fake_created)

    await leadgen._notify_lead_intake_in(pg_async_session, str(lead.id))

    assert calls and calls[0]["diagnosis_note"] == DIAGNOSIS_NOTE_NO_INPUT
    status = await pg_async_session.scalar(
        select(SalesLead.notification_status).where(SalesLead.id == lead.id)
    )
    assert status == "PENDING"
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_public_leads.py::test_create_lead_records_notification_failure tests/test_lead_followup_resolvers.py -v`
  Expected: `assert 'FAILED' == 'PENDING'`, `KeyError: 'LEAD_INTAKE_NOTIFICATION_FAILED'`.
  통합: `.venv/bin/python -m pytest tests/integration/test_lead_intake_notification_retry.py -v` → `ImportError: cannot import name 'DIAGNOSIS_NOTE_NO_INPUT' from 'app.services.inquiry_diagnosis'`.

- [ ] **Step 3: 최소 구현**

`backend/app/services/inquiry_diagnosis.py` — `PUBLIC_INTAKE_ACTOR`(33행) 위로 `leads.py:18-21`의 세 상수를 옮긴다(주석 포함, 값 그대로). 그리고:

```python
async def inquiry_diagnosis_note(db: AsyncSession, lead) -> str:
    """접수 Slack의 '자동 처리' 한 줄을 저장된 사실로 다시 만든다(drain 재발송용, 접수 API와 같은 규칙)."""
    if await active_diagnosis_for(db, lead.id) is not None:
        return DIAGNOSIS_NOTE_QUEUED
    if lead.specialty and lead.region_keyword and lead.core_keywords:
        return DIAGNOSIS_NOTE_REFUSED
    return DIAGNOSIS_NOTE_NO_INPUT
```

`backend/app/api/public/leads.py`:
- `:18-21` 상수 정의를 지우고 `from app.services.inquiry_diagnosis import (DIAGNOSIS_NOTE_NO_INPUT, DIAGNOSIS_NOTE_QUEUED, DIAGNOSIS_NOTE_REFUSED)` 추가
  (기존 이름 그대로 `leads_api.DIAGNOSIS_NOTE_*`로도 보인다).
- `:222-223` 교체:

```python
    # 실패는 끝이 아니다 — 매분 drain이 1시간 동안 다시 보내고, 그래도 못 보내면 사고로 올린다.
    lead.notification_status = "SENT" if notified else "PENDING"
    lead.notification_error = None if notified else "Slack/webhook delivery failed or is not configured."
```

`backend/app/workers/lead_diagnosis_tasks.py`:
- import: `from sqlalchemy import and_, func, or_, select, update`, `from app.services import inquiry_diagnosis` 추가.
- 상수(`:79` 아래):

```python
# 도입문의 접수 알림을 drain이 다시 보내는 기간. 넘으면 FAILED로 끝내고 운영 채널 사고로 올린다.
LEAD_INTAKE_NOTIFY_RETRY_WINDOW = timedelta(hours=1)


def _intake_notification_claimable(now: datetime):
    """무료 진단은 NULL/PENDING, 도입문의는 명시적 PENDING(접수 API가 보내는 중이면 NULL)만."""
    return or_(
        and_(
            SalesLead.source == LEAD_SOURCE_AI_DIAGNOSIS,
            or_(
                SalesLead.notification_status.is_(None),
                SalesLead.notification_status == LEAD_NOTIFICATION_PENDING,
            ),
        ),
        and_(
            func.upper(func.trim(SalesLead.source)) == LEAD_SOURCE_INQUIRY,
            SalesLead.notification_status == LEAD_NOTIFICATION_PENDING,
            SalesLead.purged_at.is_(None),
            SalesLead.created_at >= now - LEAD_INTAKE_NOTIFY_RETRY_WINDOW,
        ),
    )
```
- `_notify_lead_intake`(99-153)를 세션 주입형으로 나눈다:

```python
async def _notify_lead_intake(lead_id: str) -> dict:
    """접수 Slack을 한 번만 보내고 결과를 lead 행에 남긴다."""
    sessionmaker_ = get_async_sessionmaker()
    async with sessionmaker_() as session:
        return await _notify_lead_intake_in(session, lead_id)


async def _notify_lead_intake_in(session, lead_id: str) -> dict:
    import uuid as _uuid

    pk = _uuid.UUID(str(lead_id))
    claimed = (
        await session.execute(
            update(SalesLead)
            .where(SalesLead.id == pk, _intake_notification_claimable(datetime.now(timezone.utc)))
            .values(notification_status=LEAD_NOTIFICATION_SENDING, notification_error=None)
            .returning(SalesLead.id)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await session.rollback()
        return {"skipped": "already_claimed_or_finished"}
    await session.commit()

    lead = (await session.execute(select(SalesLead).where(SalesLead.id == pk))).scalar_one_or_none()
    if lead is None:  # pragma: no cover - claim 직후 삭제된 극단적 경쟁
        return {"skipped": "lead_deleted"}

    inquiry = (lead.source or "").strip().upper() == LEAD_SOURCE_INQUIRY
    if inquiry:
        sent = await notifier.notify_lead_created(
            clinic_name=lead.clinic_name,
            contact=lead.contact,
            admin_url=f"{settings.ADMIN_BASE_URL.rstrip('/')}/leads",
            diagnosis_note=await inquiry_diagnosis.inquiry_diagnosis_note(session, lead),
            specialty=lead.specialty,
            region_keyword=lead.region_keyword,
            source_path=lead.source_path,
            created_at=lead.created_at,
        )
    else:
        sent = await notifier.notify_lead_diagnosis_received(
            ...  # 기존 인자 그대로(:128-145)
        )
    if sent:
        lead.notification_status = LEAD_NOTIFICATION_SENT
    else:
        # 도입문의는 창 안에서 다시 보낸다(만료는 drain이 FAILED+사고로 끝낸다). 무료 진단은 기존대로 FAILED.
        lead.notification_status = LEAD_NOTIFICATION_PENDING if inquiry else LEAD_NOTIFICATION_FAILED
    lead.notification_error = None if sent else "Slack/webhook delivery failed or is not configured."
    await session.commit()
    return {"sent": sent, "lead_id": str(lead.id)}
```
- `_lead_notifications_to_dispatch`(676-690)의 where를 `_intake_notification_claimable(datetime.now(timezone.utc))` 하나로 교체.
- 만료 함수 추가:

```python
async def _expire_inquiry_notifications(session, *, now: datetime | None = None) -> list[SalesLead]:
    """창 안에 보내지 못한 도입문의 접수 알림을 FAILED로 끝낸다. 커밋은 호출자 몫."""
    observed = now or datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(SalesLead).where(
                func.upper(func.trim(SalesLead.source)) == LEAD_SOURCE_INQUIRY,
                SalesLead.notification_status == LEAD_NOTIFICATION_PENDING,
                SalesLead.created_at < observed - LEAD_INTAKE_NOTIFY_RETRY_WINDOW,
            )
        )
    ).scalars().all()
    for lead in rows:
        lead.notification_status = LEAD_NOTIFICATION_FAILED
    await session.commit()
    return list(rows)
```
- `_drain`: 첫 세션 블록의 `exhausted = await _exhausted_to_failed(session)` 다음 줄에 `expired_notices = await _expire_inquiry_notifications(session)`
  (그 함수가 커밋하므로 순서상 `await session.commit()` 앞에 둬도 무해). 사고 루프 뒤에:

```python
    for lead in expired_notices:
        try:
            await open_ops_incident(
                pipeline="lead_intake_notification",
                object_type="lead",
                object_id=str(lead.id),
                incident_type="LEAD_INTAKE_NOTIFICATION_FAILED",
                safe_error_code="LEAD_INTAKE_NOTIFICATION_FAILED",
                problem="도입문의 접수 알림을 1시간 동안 보내지 못했습니다.",
                customer_impact="새 도입문의를 담당자가 Slack으로 받지 못했습니다.",
                next_action="상담 요청 화면에서 새 문의를 확인해 담당자를 정하세요. 처리를 시작하면 자동으로 닫힙니다.",
                source_type="SALES_LEAD",
                hospital_name="도입문의",
                admin_path="/leads",
                actor="lead-intake-worker",
            )
        except Exception:  # noqa: BLE001 — 알림 실패가 드레인을 멈추지 않는다.
            logger.warning("lead intake notification failure alert delivery failed")
```
  반환 dict에 `"expired_intake_notices": len(expired_notices),` 추가.

`backend/app/workers/incident_backlog.py` — import에 `from app.models.lead import SalesLead`, `_monthly_cohort_enrolled` 아래:

```python
def _lead_followed_up(db: Session, incident: Incident, _now: datetime) -> str | None:
    # 리드 부수효과(접수 알림·안내 문자) 실패는 사람이 그 리드를 맡으면 끝난다.
    try:
        lead = db.get(SalesLead, uuid.UUID(str(incident.source_id)))
    except (TypeError, ValueError):
        return None
    if lead is None or lead.purged_at is not None:
        return "lead_purged"
    return "lead_followed_up" if (lead.status or "NEW") != "NEW" else None
```
`RESOLVERS`에 `"LEAD_INTAKE_NOTIFICATION_FAILED": _lead_followed_up,`와 `"INQUIRY_SMS_FAILED": _lead_followed_up,` 추가.

`backend/app/services/notification_copy.py` `_CATALOG`의 `"LEAD_DELIVERY_ABANDONED"` 다음에:

```python
    "LEAD_INTAKE_NOTIFICATION_FAILED": ActionCopy(
        "도입문의 접수 알림 미전달",
        "상담 요청 화면에서 새 문의를 확인하고 담당자를 정해 주세요. 처리를 시작하면 자동으로 닫힙니다.",
        "상담 요청 보기",
    ),
    "INQUIRY_SMS_FAILED": ActionCopy(
        "도입문의 안내 문자 미발송",
        "원장님께 안내 문자가 가지 않았습니다. 상담 요청 화면에서 직접 연락해 주세요.",
        "상담 요청 보기",
    ),
```

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_public_leads.py tests/test_lead_followup_resolvers.py tests/test_notification_labels.py -q` → PASS.
  통합: `.venv/bin/python -m pytest tests/integration/test_lead_intake_notification_retry.py tests/integration/test_lead_diagnosis_drain.py tests/integration/test_lead_diagnosis_intake.py tests/integration/test_incident_backlog_postgres.py -q` → PASS.
  가드: `make copy-guard`(저장소 루트) → PASS(Admin 문구 변경 없음).

- [ ] **Step 5: 커밋**
  `git add backend/app/services/inquiry_diagnosis.py backend/app/api/public/leads.py backend/app/workers/lead_diagnosis_tasks.py backend/app/workers/incident_backlog.py backend/app/services/notification_copy.py backend/tests/test_public_leads.py backend/tests/test_lead_followup_resolvers.py backend/tests/integration/test_lead_intake_notification_retry.py && git commit -m "fix(inquiry): 접수 Slack 실패는 PENDING으로 남겨 drain이 1시간 재발송하고 그래도 실패하면 사고로 올린다"`

**레지스트리 항목:** `LEAD_INTAKE_NOTIFICATION_FAILED: audience=OPERATOR, initial_state=OPEN, resolver=_lead_followed_up`.

**계약 영향:** CLAUDE.md "도입문의" 문장의 "리드 저장 뒤의 부수효과라 어느 쪽이 실패해도 접수를 되돌리지 않으며" 뒤에 "접수 Slack 실패는 PENDING으로 남아
1분 drain이 1시간 다시 보내고, 그래도 못 보내면 `LEAD_INTAKE_NOTIFICATION_FAILED` 한 건을 운영 채널로 올린다(리드를 맡으면 닫힌다)"를 추가.

**위험·롤백:** 도입문의 웹훅이 죽어 있으면 1시간 동안 매분 3번(`notifier._send` 내부 재시도)씩 시도한다(최대 180회) — 채널 생존 탐침이 같은 웹훅을 이미 감시하므로
허용. 롤백은 revert(PENDING으로 남은 행은 옛 drain이 안 집으므로 무해).

---

### Task 1-9c-2: 도입문의 SMS는 '닿지 않은' 실패만 1회 재시도하고 최종 실패를 사고로 올린다

**Files:**
- Modify: `backend/app/services/inquiry_sms.py:56-66, 157-180`
- Modify: `backend/app/api/public/leads.py:225-229`
- Test: `backend/tests/test_inquiry_sms.py`(추가·수정), `backend/tests/test_public_leads.py`(추가)

**조사 결과:**
- `deliver`는 `httpx.HTTPError`를 잡아 FAILED로 돌려주고(`inquiry_sms.py:157-169`) 재시도·알림이 없다. 리드 행에 `ack_sms_status=FAILED`만 남는다.
- 미설정 SKIPPED는 이미 readiness 참고 사실로 보인다(`utils/production_readiness.py:342,348-362` `facts.inquiry_sms`) → 감사 요구("SKIPPED는 조용히,
  readiness에 보임")는 **이미 충족**. 변경 불필요.
- 재시도 안전성: `ConnectError`·`ConnectTimeout`은 요청이 사업자에 닿지 않았다. 429·503은 처리 거절이다. 읽기 시간 초과·다른 5xx는 이미 발송됐을 수 있어 재시도하면
  원장에게 문자가 두 번 간다 → 재시도하지 않는다.
- 기존 테스트 `test_acknowledge_records_provider_failure_without_raising`(ConnectTimeout)은 재시도 뒤에도 FAILED·`ack_sms_error=="ConnectTimeout"`이라 기대값 유지,
  호출 수만 2가 된다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_inquiry_sms.py` 끝에 추가

```python
class _SequenceClient(_FakeClient):
    def __init__(self, outcomes):
        super().__init__()
        self._outcomes = list(outcomes)

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_OK = httpx.Response(200, json={"header": {"isSuccessful": True}, "body": {"data": {"sendResultList": [{"resultCode": 0}]}}})


async def test_a_connect_failure_is_retried_once_and_can_succeed(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.ConnectError("refused"), _OK])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), _lead())

    assert outcome.sent
    assert len(client.calls) == 2


async def test_a_read_timeout_is_not_retried_because_the_text_may_have_gone_out(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.ReadTimeout("slow"), _OK])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), _lead())

    assert outcome.status == "FAILED"
    assert len(client.calls) == 1


async def test_a_503_is_retried_once(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)
    client = _SequenceClient([httpx.Response(503), httpx.Response(503)])
    monkeypatch.setattr(inquiry_sms.httpx, "AsyncClient", lambda **kwargs: client)

    outcome = await inquiry_sms.acknowledge_inquiry(FakeDB(), _lead())

    assert outcome.status == "FAILED"
    assert outcome.detail == "HTTP 503"
    assert len(client.calls) == 2
```

`test_acknowledge_records_provider_failure_without_raising`(151-162)에 `monkeypatch.setattr(inquiry_sms, "INQUIRY_SMS_RETRY_DELAY_SECONDS", 0)`을 `_configure` 다음 줄에 넣는다.

`backend/tests/test_public_leads.py` 끝에 추가:

```python
async def test_a_failed_sms_opens_one_operator_incident_after_the_response(monkeypatch):
    _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "FAILED")
    opened = []

    async def fake_open(**kwargs):
        opened.append(kwargs)

    monkeypatch.setattr(leads_api, "open_ops_incident", fake_open)
    tasks = BackgroundTasks()

    await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=tasks,
        db=FakeDB(),
    )
    await tasks()

    assert [item["incident_type"] for item in opened] == ["INQUIRY_SMS_FAILED"]
    assert opened[0]["object_id"] == "lead-id"
    assert opened[0]["hospital_name"] == "도입문의"  # 병원명·연락처를 사고 제목에 싣지 않는다


async def test_skipped_sms_opens_nothing(monkeypatch):
    _silence_notifier(monkeypatch)
    _sms_result(monkeypatch, "SKIPPED")
    monkeypatch.setattr(leads_api, "open_ops_incident", lambda **_k: pytest.fail("no incident"))
    tasks = BackgroundTasks()

    await _create_lead(
        request=FakeRequest(),
        body=_inquiry_body(specialty=None, region_keyword=None, core_keywords=None),
        background_tasks=tasks,
        db=FakeDB(),
    )
    await tasks()
```

- [ ] **Step 2: 실패 확인**
  Run: `.venv/bin/python -m pytest tests/test_inquiry_sms.py tests/test_public_leads.py -k "retried or read_timeout or 503 or failed_sms or skipped_sms" -v`
  Expected: `AttributeError: ... 'INQUIRY_SMS_RETRY_DELAY_SECONDS'`, `AttributeError: module 'app.api.public.leads' has no attribute 'open_ops_incident'`.

- [ ] **Step 3: 최소 구현**

`backend/app/services/inquiry_sms.py`:
- `import asyncio` 추가, `SMS_SKIPPED` 아래:

```python
# 요청이 사업자에 닿지 않았거나(연결 실패) 처리를 거절한(429·503) 경우만 다시 보낸다. 읽기 시간 초과·
# 그 밖의 5xx는 이미 발송됐을 수 있어 재시도하면 원장 휴대전화에 문자가 두 번 간다.
_RETRYABLE_TRANSPORT = (httpx.ConnectError, httpx.ConnectTimeout)
_RETRYABLE_STATUS = frozenset({429, 503})
INQUIRY_SMS_RETRY_DELAY_SECONDS = 0.5
```
- `deliver`(157-180)를 둘로 나눈다:

```python
async def _deliver_once(to: str) -> tuple[AckSmsOutcome, bool]:
    """(결과, 다시 보내도 안전한가)."""
    url, headers, payload = build_nhn_request(to=to)
    try:
        async with httpx.AsyncClient(timeout=settings.INQUIRY_SMS_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        # 시크릿이 섞일 수 있는 URL·본문은 기록하지 않는다. 예외 종류만으로 원인 분류는 된다.
        logger.error("inquiry SMS to %s failed: %s", mask_mobile(to), exc.__class__.__name__)
        return AckSmsOutcome(SMS_FAILED, exc.__class__.__name__), isinstance(exc, _RETRYABLE_TRANSPORT)
    try:
        body = response.json()
    except ValueError:
        body = None
    outcome = parse_nhn_response(response.status_code, body)
    if outcome.sent:
        logger.info("inquiry SMS sent to %s request_id=%s", mask_mobile(to), outcome.request_id)
    else:
        logger.error("inquiry SMS to %s rejected: %s", mask_mobile(to), outcome.detail)
    return outcome, response.status_code in _RETRYABLE_STATUS


async def deliver(to: str) -> AckSmsOutcome:
    """NHN Cloud에 1건 발송. 닿지 않은 실패만 1회 다시 보낸다. 예외를 밖으로 내지 않는다."""
    outcome, retryable = await _deliver_once(to)
    if outcome.sent or not retryable:
        return outcome
    await asyncio.sleep(INQUIRY_SMS_RETRY_DELAY_SECONDS)
    outcome, _ = await _deliver_once(to)
    return outcome
```

`backend/app/api/public/leads.py`:
- import에 `from app.services.ops_incident_alerts import open_ops_incident` 추가.
- 함수 위(모듈 수준)에:

```python
async def _open_inquiry_sms_incident(lead_id: str) -> None:
    """안내 문자가 끝내 나가지 못했다 — 원장님께 사람이 직접 연락해야 한다. 응답 뒤에 연다."""
    try:
        await open_ops_incident(
            pipeline="inquiry_sms",
            object_type="lead",
            object_id=lead_id,
            incident_type="INQUIRY_SMS_FAILED",
            safe_error_code="INQUIRY_SMS_FAILED",
            problem="도입문의 접수 안내 문자를 보내지 못했습니다.",
            customer_impact="원장님이 접수 안내 문자를 받지 못했습니다.",
            next_action="상담 요청 화면에서 원장님께 직접 연락하세요. 처리를 시작하면 자동으로 닫힙니다.",
            source_type="SALES_LEAD",
            # 병원명·연락처를 사고 제목·Slack에 새로 싣지 않는다(처리방침 고지 범위 유지).
            hospital_name="도입문의",
            admin_path="/leads",
            actor="public-inquiry",
        )
    except Exception:  # noqa: BLE001 - 사고 기록 실패가 접수 응답을 바꾸지 않는다.
        logger.warning("inquiry SMS failure incident could not be recorded for lead %s", lead_id)
```
- `:227-228` 다음:

```python
    sms = await inquiry_sms.acknowledge_inquiry(db, lead)
    await db.commit()
    if sms.status == inquiry_sms.SMS_FAILED:
        background_tasks.add_task(_open_inquiry_sms_incident, str(lead.id))
```

- [ ] **Step 4: 통과 확인**
  Run: `.venv/bin/python -m pytest tests/test_inquiry_sms.py tests/test_public_leads.py tests/test_production_readiness.py -q`
  Expected: 전부 PASS.

- [ ] **Step 5: 커밋**
  `git add backend/app/services/inquiry_sms.py backend/app/api/public/leads.py backend/tests/test_inquiry_sms.py backend/tests/test_public_leads.py && git commit -m "fix(inquiry): 닿지 않은 안내 문자만 1회 재시도하고 최종 실패는 운영자 사고로 올린다"`

**레지스트리 항목:** `INQUIRY_SMS_FAILED: audience=OPERATOR, initial_state=OPEN, resolver=_lead_followed_up`(Task 1-9c-1에서 이미 등록).

**계약 영향:** CLAUDE.md "도입문의" 문장에 "안내 문자는 연결 실패·429·503만 1회 다시 보내고(이중 발송 방지), 최종 실패는 `INQUIRY_SMS_FAILED` 한 건으로 사람에게 알린다.
미설정 SKIPPED는 사고 없이 readiness `facts.inquiry_sms`에만 보인다"를 추가.

**위험·롤백:** 사업자 장애 시 접수 응답이 최대 `INQUIRY_SMS_TIMEOUT_SECONDS`×2+0.5초 늦어진다(재시도는 연결 실패라 보통 빠르게 끝남). 롤백은 revert.

---

## 대표 확인이 필요한 질문

1. **월 SoV 비용 경고선(1-6c)**: 경고선을 월 상한(4260)에서 계산하면 28곳이라 9곳 규모에서는 경고가 사실상 꺼진다. 실제 지출 차단은 비용 가드가 하므로 이것이 맞다고
   보지만, "병원 수가 몇 곳을 넘으면 알려 달라"는 별도 사업 기준이 있으면 그 숫자를 받아 상수로 둔다.
2. **V0 측정 재사용 창(1-9a)**: 자동 재실행이 같은 요청의 완료 측정을 최대 7일까지 재사용한다(지금은 6시간). 다시 사지 않는 대신 리포트의 측정 시점이 최대 며칠 앞설 수 있다.
   허용 범위(예: 24시간)를 정해 주면 그 값으로 줄인다 — 그보다 짧으면 6시간 간격 재실행이 매번 150호출을 다시 산다.
3. **코호트 밖 달의 원장 안내(1-7a)**: 매니페스트 없는 달은 "이번 달은 전달 불가, 원장님께 안내"로 AE에게 알린다. 그 달을 원장에게 어떻게 설명할지(측정 없음 안내 문구,
   또는 주간 자료로 만든 내부용만 공유) 정책이 필요하다.
