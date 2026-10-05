"""독립 검수 장애(`CONTENT_AI_REVIEW_UNAVAILABLE`)의 자동 재검수는 물러서며, 글마다 한도가 있고, 한도에
닿으면 사람을 부른다(PR-B 2).

종전에는 KST 하루 4회 예산이 매일 영원히 초기화됐다(`DAILY_RESET_ENVIRONMENT_CODES`). 같은 후보가 잘린
응답(`stop_reason=length`)·형식이 깨진 출력으로 매번 실패해도 01·04·07·12시마다 유료 재검수를 사고,
사람에게는 끝내 올라오지 않았다(2026-10-03 사고). 이제:

- 같은 후보(제목·본문·메타·FAQ·참고자료, `candidate_sha256`)의 연속 UNAVAILABLE을 KST 날을 넘어 센다
  (`review_unavailable_total`·`review_unavailable_candidate`). 검수가 끝나면(기록 삭제)·후보가
  바뀌면·원인이 바뀌면 0부터 다시 센다.
- n번째 실패 뒤 다음 시도는 `관측 + min(1h·2^(n-1), 24h)` 이후에 그 슬롯을 실제로 집는 첫 스윕이다
  (`REVIEW_UNAVAILABLE_BACKOFF_BASE`·`REVIEW_UNAVAILABLE_BACKOFF_MAX`). 일일 초기화 분기도 따른다.
- `settings.CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES`(기본 6)에 닿으면 OPERATOR_REQUIRED·기한 없음·
  `review_unavailable_cap_reached`이고, 그 후보로는 어떤 스윕도 다시 사지 않는다. 인시던트는 OPEN(사람의
  일)이고 기존 Slack 정책·라벨(`[Error : 오류 발생]`)로 알린다.
- 비용 가드 보류(COST_BLOCKED)와 설정 오류(CONTENT_AI_REVIEW_CONFIG_ERROR)는 종전 그대로다.
- 어떤 재시도도 게이트를 우회하지 않는다 — UNAVAILABLE은 PASS가 아니고, 통과한 재검수도 바뀌지 않은
  게이트를 다시 거친다(PR-B 4c·4d·4e).

네트워크·DB·공급자는 쓰지 않는다 — 가짜 세션·가짜 검수자만 쓴다.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import arrow
import pytest

from app.api.admin.operations_center_serializers import requires_operator_action
from app.core import config
from app.core.config import Settings
from app.models.operations import IncidentState
from app.services.content_ai_review import (
    ContentAiReview,
    ContentAiReviewStatus,
    ContentAiReviewUnavailableReason,
    candidate_sha256,
)
from app.services.content_publication import assess_content_publication
from app.services.notification_labels import ERROR_LABEL
from app.services.reference_verification import ReferenceVerifier
from app.workers import (
    generation_incident_control,
    generation_retry_policy,
    nightly_generation_batch,
    tasks,
)
from app.workers.generation_attempt_state import GENERATION_ATTEMPT_KEY
from app.workers.generation_retry_policy import (
    ENVIRONMENT_ATTEMPT_BUDGET,
    RECOVERY_SWEEP_HOURS,
    GenerationRetryClass,
    recovery_is_abandoned,
    retry_is_due,
    sweep_claims_slot,
)
from tests.reference_fetch_doubles import PageFetcher
from tests.test_generation_incident_copy import _incident, _Session
from tests.test_tasks_nightly import (
    _approved_philosophy,
    _AutoPublishDB,
    _NightlyTaskDB,
    _publication_hospital,
    _publication_item,
)

KST = ZoneInfo("Asia/Seoul")
SLOT = date(2026, 6, 10)  # `_publication_item`의 예정일
CODE = "CONTENT_AI_REVIEW_UNAVAILABLE"
MAX_RETRIES = "CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES"


def _kst(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


def _clock(monkeypatch, moment: datetime) -> None:
    """시도 기록·재시도 정책·인시던트·발행기가 같은 '지금'을 보게 한다."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

    for module in (tasks, generation_retry_policy, generation_incident_control, nightly_generation_batch):
        monkeypatch.setattr(module, "datetime", _Frozen)
    monkeypatch.setattr(tasks.arrow, "now", lambda *_a, **_k: arrow.get(moment))


def _policy(name: str):
    value = getattr(generation_retry_policy, name, None)
    assert value is not None, f"generation_retry_policy.{name}이(가) 없다"
    return value


def _unavailable_review(item, reason="INVALID_RESPONSE") -> dict:
    return {
        "status": "UNAVAILABLE",
        "blocking": True,
        "findings": [],
        "summary": "검수 응답이 잘렸습니다.",
        "model": "reviewer-test",
        "schema_version": 2,
        "candidate_sha256": candidate_sha256(item),
        "unavailable_reason": reason,
    }


def _stored_post(philosophy, *, image=True):
    """본문·참고자료·인증 이미지가 있고 독립 검수만 UNAVAILABLE로 끝난 글(저장 본문 재검수 대상)."""

    item = _publication_item(_publication_hospital(), body="진료 전 확인할 점을 안내합니다.")
    item.content_philosophy_id = philosophy.id
    item.content_revision = 3
    if not image:
        for field in (
            "image_url",
            "image_content_hash",
            "image_subject_hash",
            "image_policy_version",
            "image_policy_verified_at",
        ):
            setattr(item, field, None)
    item.essence_check_summary = {
        "automatic_remediation_attempts": 0,
        "ai_review": _unavailable_review(item),
    }
    return item


def _attempt(item) -> dict:
    return dict(item.essence_check_summary.get(GENERATION_ATTEMPT_KEY) or {})


def _remember(monkeypatch, item, moment, reason=CODE, philosophy=None) -> dict:
    _clock(monkeypatch, moment)
    return tasks._remember_generation_attempt(
        _NightlyTaskDB(), item, philosophy or SimpleNamespace(id="p1"), reason
    )


def _review(status, reason=None):
    return ContentAiReview(
        status=status,
        confidence=0.0 if status is ContentAiReviewStatus.UNAVAILABLE else 0.95,
        findings=(),
        summary="재검수",
        model="reviewer-test",
        unavailable_reason=reason,
    )


def _arm_sweep(monkeypatch, philosophy, outcome):
    """저장 본문 재검수 경로만 실제로 돈다. 검수자는 `outcome`을 돌려주고 호출 시각을 남긴다."""

    calls: list[datetime] = []

    async def reviewer(**kwargs):
        calls.append(tasks.datetime.now(UTC))
        assert kwargs["content"]["body"]
        return outcome() if callable(outcome) else outcome

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(tasks, "review_generated_content", reviewer)
    return calls


def _sweep_moments(first: date, last: date):
    day = first
    while day <= last:
        for hour in sorted(RECOVERY_SWEEP_HOURS):
            moment = _kst(day, hour)
            if sweep_claims_slot(moment, SLOT):
                yield moment
        day += timedelta(days=1)


def _run_sweeps(monkeypatch, item, moments, *, after_each=None):
    db = _NightlyTaskDB()
    for moment in moments:
        _clock(monkeypatch, moment)
        tasks._generate_single_content_item(db, item, item.hospital)
        if after_each is not None:
            after_each(moment)


def _due(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


# ── 설정과 상수 ───────────────────────────────────────────────────────────────────


def test_the_retry_cap_setting_defaults_to_six():
    assert MAX_RETRIES in Settings.model_fields, "Settings에 검수 장애 재시도 한도 설정이 없다"
    assert Settings.model_fields[MAX_RETRIES].default == 6
    assert getattr(config.settings, MAX_RETRIES) >= 1


@pytest.mark.parametrize("value", [0, -1])
def test_the_retry_cap_setting_rejects_values_below_one(value):
    with pytest.raises(ValueError):
        Settings(APP_ENV="development", **{MAX_RETRIES: value})


def test_the_retry_cap_setting_accepts_one():
    assert getattr(Settings(APP_ENV="development", **{MAX_RETRIES: 1}), MAX_RETRIES) == 1


def test_the_backoff_doubles_from_one_hour_and_stops_at_a_day():
    assert _policy("REVIEW_UNAVAILABLE_BACKOFF_BASE") == timedelta(hours=1)
    assert _policy("REVIEW_UNAVAILABLE_BACKOFF_MAX") == timedelta(hours=24)
    backoff = _policy("review_unavailable_backoff")
    assert [backoff(n) for n in range(1, 8)] == [
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=4),
        timedelta(hours=8),
        timedelta(hours=16),
        timedelta(hours=24),
        timedelta(hours=24),
    ]
    assert backoff(40) == timedelta(hours=24)


# ── 계수: 같은 후보의 연속 실패를 KST 날을 넘어 센다 ─────────────────────────────────


def test_consecutive_outages_of_one_candidate_are_counted_across_kst_days(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    totals = []
    for moment in (
        _kst(SLOT, 1), _kst(SLOT, 4), _kst(SLOT, 7),
        _kst(SLOT + timedelta(days=1), 1),  # 하루 4회 예산은 초기화되지만 이 계수는 아니다
    ):
        attempt = _remember(monkeypatch, item, moment)
        totals.append(attempt.get("review_unavailable_total"))
        assert attempt.get("review_unavailable_candidate") == candidate_sha256(item)
        assert not attempt.get("review_unavailable_cap_reached")

    assert totals == [1, 2, 3, 4]
    assert attempt["provider_attempt_count"] == 1  # 종전 일일 예산 계수는 그대로 초기화된다
    assert attempt["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value


def test_a_changed_candidate_starts_the_count_again(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    for hour in (1, 4, 7):
        _remember(monkeypatch, item, _kst(SLOT, hour))
    assert _attempt(item).get("review_unavailable_total") == 3

    item.body = "고쳐 쓴 본문입니다. 진료 전 확인할 점을 다시 정리했습니다."
    attempt = _remember(monkeypatch, item, _kst(SLOT, 12))

    assert attempt.get("review_unavailable_total") == 1
    assert attempt.get("review_unavailable_candidate") == candidate_sha256(item)


def test_another_cause_in_between_starts_the_count_again(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    for hour in (1, 4, 7):
        _remember(monkeypatch, item, _kst(SLOT, hour))
    assert _attempt(item).get("review_unavailable_total") == 3

    _remember(monkeypatch, item, _kst(SLOT, 12), reason="COST_BLOCKED")
    attempt = _remember(monkeypatch, item, _kst(SLOT, 18))

    assert attempt.get("review_unavailable_total") == 1


def test_a_completed_review_starts_the_count_again(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    for hour in (1, 4, 7):
        _remember(monkeypatch, item, _kst(SLOT, hour))
    assert _attempt(item).get("review_unavailable_total") == 3

    tasks._clear_generation_attempt(_NightlyTaskDB(), item)  # 검수가 끝나면 기록이 지워진다
    attempt = _remember(monkeypatch, item, _kst(SLOT, 12))

    assert attempt.get("review_unavailable_total") == 1


# ── 물러서기: 다음 시도는 백오프 이후에 그 슬롯을 집는 첫 스윕이다 ──────────────────────


def test_the_backoff_moment_itself_is_a_valid_next_attempt(monkeypatch):
    """두 번째 실패(10:00, 2시간)의 하한은 12:00이고 12:00 스윕이 이 슬롯을 집는다 — 경계를 포함한다."""

    item = _stored_post(SimpleNamespace(id="p1"))
    first = _remember(monkeypatch, item, _kst(SLOT, 9))
    second = _remember(monkeypatch, item, _kst(SLOT, 10))

    assert first["next_retry_at"] == _due(_kst(SLOT, 12))  # 10:00 이후 첫 스윕
    assert second["next_retry_at"] == _due(_kst(SLOT, 12))  # 정확히 12:00
    assert retry_is_due(second, _kst(SLOT, 11, 59)) is False
    assert retry_is_due(second, _kst(SLOT, 12)) is True


def test_the_daily_reset_branch_honours_the_backoff():
    """어제 예산을 다 쓰고 '집을 스윕이 없다'로 저장된 기록도, 새 날이라고 백오프보다 먼저 다시 사지 않는다.

    다섯 번째 연속 실패(9/19 23:00 KST)의 하한은 16시간 뒤인 9/20 15:00이다. 종전 일일 초기화 분기는
    9/20 01:00에 바로 다시 샀다.
    """

    observed = _kst(date(2026, 9, 19), 23)
    attempt = {
        "reason": CODE,
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": ENVIRONMENT_ATTEMPT_BUDGET,
        "attempt_period": "2026-09-19",
        "observed_at": observed.astimezone(UTC).isoformat(),
        "next_retry_at": None,
        "review_unavailable_total": 5,
        "review_unavailable_candidate": "c" * 64,
    }

    assert retry_is_due(attempt, _kst(date(2026, 9, 20), 1)) is False
    assert retry_is_due(attempt, _kst(date(2026, 9, 20), 12)) is False
    assert retry_is_due(attempt, _kst(date(2026, 9, 20), 15)) is True


def test_a_legacy_review_outage_record_keeps_the_daily_reset():
    """보존: 계수가 없는 종전 기록은 종전 일일 초기화 그대로다(2026-09-20 사고 보정)."""

    attempt = {
        "reason": CODE,
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": ENVIRONMENT_ATTEMPT_BUDGET,
        "attempt_period": "2026-09-19",
        "next_retry_at": None,
    }

    assert retry_is_due(attempt, _kst(date(2026, 9, 20), 1)) is True


# ── 한도: 사람의 일로 넘기고 어떤 스윕도 다시 사지 않는다 ─────────────────────────────


def test_the_sweeps_back_off_and_stop_at_the_cap(monkeypatch):
    """(PR-B 4d) 매 스윕이 같은 후보를 재검수하고 매번 잘린 응답으로 실패한다. 종전에는 하루 4회씩
    영원히 샀다. 이제 6회째에 멈추고, 그 뒤 며칠을 돌아도 검수자를 다시 부르지 않는다."""

    philosophy = _approved_philosophy()
    item = _stored_post(philosophy)
    calls = _arm_sweep(
        monkeypatch,
        philosophy,
        _review(
            ContentAiReviewStatus.UNAVAILABLE, ContentAiReviewUnavailableReason.INVALID_RESPONSE
        ),
    )
    ladder: list[dict] = []

    def remember_new_attempts(_moment):
        if len(calls) > len(ladder):
            ladder.append(copy.deepcopy(_attempt(item)))
        # 매 스윕 뒤에도 게이트는 이 글을 공개 가능으로 보지 않는다(PR-B 4c).
        assessment = assess_content_publication(item, philosophy)
        assert assessment.publishable is False and assessment.code == CODE

    day1, day2 = SLOT - timedelta(days=1), SLOT
    _run_sweeps(
        monkeypatch,
        item,
        _sweep_moments(day1, SLOT + timedelta(days=4)),
        after_each=remember_new_attempts,
    )

    expected_calls = [
        _kst(day1, 1), _kst(day1, 4), _kst(day1, 7), _kst(day1, 12),
        _kst(day2, 1), _kst(day2, 18),
    ]
    assert calls == [moment.astimezone(UTC) for moment in expected_calls]
    assert [entry.get("review_unavailable_total") for entry in ladder] == [1, 2, 3, 4, 5, 6]
    # 1h→04:00, 2h→07:00, 4h→12:00, 8h(오늘 예산 소진)→다음 날 01:00, 16h→18:00, 한도→없음
    assert [entry.get("next_retry_at") for entry in ladder] == [
        _due(_kst(day1, 4)),
        _due(_kst(day1, 7)),
        _due(_kst(day1, 12)),
        _due(_kst(day2, 1)),
        _due(_kst(day2, 18)),
        None,
    ]
    capped = _attempt(item)
    assert capped["reason"] == CODE
    assert capped["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert capped.get("review_unavailable_cap_reached") is True
    assert capped.get("review_unavailable_candidate") == candidate_sha256(item)
    # 일일 초기화도, 로더 적격도 한도를 되살리지 않는다.
    next_day = _kst(SLOT + timedelta(days=1), 1)
    assert retry_is_due(capped, next_day) is False
    assert recovery_is_abandoned(capped, next_day) is True
    _clock(monkeypatch, next_day)
    assert tasks._generation_attempt_is_unchanged(item, philosophy) is True


def test_the_cap_follows_the_setting(monkeypatch):
    assert MAX_RETRIES in Settings.model_fields, "Settings에 검수 장애 재시도 한도 설정이 없다"
    monkeypatch.setattr(config.settings, MAX_RETRIES, 2)
    item = _stored_post(SimpleNamespace(id="p1"))

    first = _remember(monkeypatch, item, _kst(SLOT, 1))
    second = _remember(monkeypatch, item, _kst(SLOT, 4))

    assert first["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert not first.get("review_unavailable_cap_reached")
    assert second["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert second.get("review_unavailable_cap_reached") is True
    assert second["next_retry_at"] is None


def test_exactly_one_below_the_cap_still_retries(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    moments = [
        _kst(SLOT, 1), _kst(SLOT, 4), _kst(SLOT, 7), _kst(SLOT, 12),
        _kst(SLOT + timedelta(days=1), 1),
    ]
    for moment in moments:
        attempt = _remember(monkeypatch, item, moment)

    assert attempt.get("review_unavailable_total") == 5
    assert attempt["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert isinstance(attempt["next_retry_at"], str)
    assert not attempt.get("review_unavailable_cap_reached")


def test_after_the_cap_the_post_stays_blocked_and_unpublished(monkeypatch):
    """(PR-B 4d) 한도에 닿은 글은 발행기가 매시 그대로 막는다 — 검수를 다시 사지도 공개하지도 않는다."""

    philosophy = _approved_philosophy()
    item = _stored_post(philosophy)
    for moment in _sweep_moments(SLOT - timedelta(days=1), SLOT):
        _remember(monkeypatch, item, moment)
    assert _attempt(item).get("review_unavailable_cap_reached") is True

    db = _AutoPublishDB(item, item.hospital)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)

    async def reviewer_must_not_run(**_kwargs):
        raise AssertionError("발행기는 검수를 사지 않는다")

    monkeypatch.setattr(tasks, "review_generated_content", reviewer_must_not_run)
    _clock(monkeypatch, _kst(SLOT, 23))

    payload = tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(PageFetcher()))

    assert payload["kind"] == "blocked" and payload["code"] == CODE
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None
    assert _attempt(item).get("review_unavailable_cap_reached") is True  # 게이트가 덮어쓰지 않는다


def test_a_content_edit_after_the_cap_reopens_one_automatic_review(monkeypatch):
    philosophy = _approved_philosophy()
    item = _stored_post(philosophy)
    calls = _arm_sweep(
        monkeypatch,
        philosophy,
        _review(
            ContentAiReviewStatus.UNAVAILABLE, ContentAiReviewUnavailableReason.PROVIDER_ERROR
        ),
    )
    _run_sweeps(monkeypatch, item, _sweep_moments(SLOT - timedelta(days=1), SLOT))
    assert _attempt(item).get("review_unavailable_cap_reached") is True
    bought = len(calls)

    # 사람이 본문을 고쳤다 — 새 후보다.
    item.body = "고쳐 쓴 본문입니다. 진료 전 확인할 점을 다시 정리했습니다."
    _run_sweeps(monkeypatch, item, [_kst(SLOT + timedelta(days=1), 1)])

    assert len(calls) == bought + 1
    attempt = _attempt(item)
    assert attempt.get("review_unavailable_total") == 1
    assert attempt.get("review_unavailable_candidate") == candidate_sha256(item)


# ── 사람에게 올린다: OPEN 인시던트 + 기존 Slack 정책·라벨 ───────────────────────────────


def _capped_slot(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    item.topic_swap_history = []
    item.generation_claim_token = None
    item.generation_claimed_at = None
    for moment in _sweep_moments(SLOT - timedelta(days=1), SLOT):
        _remember(monkeypatch, item, moment)
    return item


def _below_cap_slot(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    item.topic_swap_history = []
    item.generation_claim_token = None
    item.generation_claimed_at = None
    _remember(monkeypatch, item, _kst(SLOT, 1))
    return item


async def _open_incident(monkeypatch, item, *, notify: bool, moment: datetime):
    """인시던트 저장소·outbox만 바꾸고 상태·기한·문구·알림 결정은 실제 경로로 돌린다."""

    captured: dict[str, object] = {"notifications": []}
    opened = _incident(CODE, state=IncidentState.OPEN.value)
    opened.incident_type = "CONTENT_GENERATION_FAILED"

    async def capture_request(_db, request, **_kwargs):
        captured["request"] = request
        opened.next_action = request.next_action
        opened.customer_impact = request.customer_impact
        opened.severity = request.severity
        opened.admin_path = request.admin_path
        opened.hospital_id = request.hospital_id
        return opened

    async def retrying(_db, _incident_id, **_kwargs):
        if opened.state != IncidentState.OPEN.value:
            return None
        opened.state = IncidentState.RETRYING.value
        return opened

    async def enqueue(_db, notification):
        captured["notifications"].append(notification)

    _clock(monkeypatch, moment)
    monkeypatch.setattr(
        generation_incident_control, "get_async_sessionmaker", lambda: lambda: _Session(item)
    )
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", capture_request)
    monkeypatch.setattr(generation_incident_control, "mark_retrying", retrying)
    monkeypatch.setattr(generation_incident_control, "enqueue_notification", enqueue)
    await generation_incident_control.open_generation_incident(
        item_id=item.id,
        hospital_id=item.hospital_id,
        hospital_name="검수의원",
        run_id=uuid.uuid4(),
        code=CODE,
        message="독립 검수 공급자 복구 후 자동 재검수를 다시 시도합니다.",
        notify=notify,
    )
    return captured, opened


async def test_below_the_cap_the_outage_stays_automatic_recovery(monkeypatch):
    """보존: 한도 전의 검수 장애는 RETRYING(자동 복구)이고 아무도 부르지 않는다."""

    item = _below_cap_slot(monkeypatch)
    moment = _kst(SLOT, 2)  # 01:00 실패, 다음 시도는 기한 안(04:00)이다

    captured, incident = await _open_incident(monkeypatch, item, notify=True, moment=moment)

    assert incident.state == IncidentState.RETRYING.value
    assert not requires_operator_action(incident.state, incident.sla_due_at, moment)
    assert captured["notifications"] == []


async def test_the_cap_opens_an_operator_incident(monkeypatch):
    item = _capped_slot(monkeypatch)
    moment = _kst(SLOT, 23, 30)

    captured, incident = await _open_incident(monkeypatch, item, notify=False, moment=moment)

    assert incident.state == IncidentState.OPEN.value
    assert incident.sla_due_at is None
    assert requires_operator_action(incident.state, incident.sla_due_at, moment)
    action = captured["request"].next_action
    assert "재시도 중" not in action  # 종전 '시스템 재시도 중입니다' 문구가 아니다
    assert "한도" in action or "횟수" in action
    assert "확인" in action


async def test_the_cap_notifies_once_under_the_error_label(monkeypatch):
    item = _capped_slot(monkeypatch)

    captured, incident = await _open_incident(
        monkeypatch, item, notify=True, moment=_kst(SLOT, 23, 30)
    )

    assert incident.state == IncidentState.OPEN.value
    notifications = captured["notifications"]
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.notification_type == "INCIDENT_OPEN"
    assert notification.message.fallback_text.startswith(ERROR_LABEL)


# ── 종전 그대로: 비용 가드 보류와 설정 오류 ──────────────────────────────────────────


def test_cost_guard_deferrals_keep_their_behaviour(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))
    for offset in range(8):  # 한도보다 많이, 여러 날에 걸쳐
        attempt = _remember(
            monkeypatch, item, _kst(SLOT + timedelta(days=offset // 4), (1, 4, 7, 12)[offset % 4]),
            reason="COST_BLOCKED",
        )

    assert attempt["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert attempt["provider_attempt_count"] == 0
    assert attempt["guard_deferral_count"] >= 4
    assert isinstance(attempt["next_retry_at"], str)
    assert "review_unavailable_total" not in attempt
    assert not attempt.get("review_unavailable_cap_reached")


def test_a_cost_guarded_review_is_never_capped_by_the_sweeps(monkeypatch):
    """비용 가드가 매번 재검수를 미뤄도 그것은 검수 장애가 아니다 — 한도로 사람을 부르지 않는다."""

    philosophy = _approved_philosophy()
    item = _stored_post(philosophy)
    item.essence_check_summary["ai_review"] = _unavailable_review(item, reason="COST_BLOCKED")
    calls = _arm_sweep(
        monkeypatch,
        philosophy,
        _review(ContentAiReviewStatus.UNAVAILABLE, ContentAiReviewUnavailableReason.COST_BLOCKED),
    )

    _run_sweeps(monkeypatch, item, _sweep_moments(SLOT - timedelta(days=1), SLOT + timedelta(days=1)))

    attempt = _attempt(item)
    assert len(calls) > 6  # 기본 한도보다 많이 샀다 — 비용 가드 보류는 이 한도로 세지 않는다
    assert attempt["reason"] == "COST_BLOCKED"
    assert attempt["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert not attempt.get("review_unavailable_cap_reached")


def test_a_configuration_error_keeps_its_behaviour(monkeypatch):
    item = _stored_post(SimpleNamespace(id="p1"))

    attempt = _remember(monkeypatch, item, _kst(SLOT, 1), reason="CONTENT_AI_REVIEW_CONFIG_ERROR")

    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert "review_unavailable_total" not in attempt
    assert not attempt.get("review_unavailable_cap_reached")


def test_the_unavailable_reasons_still_map_to_the_same_codes():
    def code(reason):
        return tasks._content_ai_unavailable_code(
            _review(ContentAiReviewStatus.UNAVAILABLE, reason)
        )

    assert code(ContentAiReviewUnavailableReason.COST_BLOCKED) == "COST_BLOCKED"
    assert code(ContentAiReviewUnavailableReason.PROVIDER_UNCONFIGURED) == (
        "CONTENT_AI_REVIEW_CONFIG_ERROR"
    )
    assert code(ContentAiReviewUnavailableReason.INVALID_RESPONSE) == CODE
    assert code(ContentAiReviewUnavailableReason.PROVIDER_ERROR) == CODE


# ── 우회 없음: UNAVAILABLE은 PASS가 아니고, 통과한 재검수도 게이트를 다시 거친다 ──────────


def _publish_once(monkeypatch, item, philosophy, moment):
    db = _AutoPublishDB(item, item.hospital)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_a: philosophy)
    monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)
    _clock(monkeypatch, moment)
    return tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(PageFetcher()))


def test_an_outage_retry_never_makes_the_post_publishable(monkeypatch):
    """(PR-B 4c) 인증 이미지까지 갖춘 글 — 검수 게이트만 막고 있다. 재검수가 실패하는 동안 발행기는
    매번 막는다."""

    philosophy = _approved_philosophy()
    item = _stored_post(philosophy)
    _arm_sweep(
        monkeypatch,
        philosophy,
        _review(
            ContentAiReviewStatus.UNAVAILABLE, ContentAiReviewUnavailableReason.INVALID_RESPONSE
        ),
    )
    for hour in (1, 4, 7):
        _run_sweeps(monkeypatch, item, [_kst(SLOT, hour)])
        payload = _publish_once(monkeypatch, item, philosophy, _kst(SLOT, hour + 1))
        assert payload["kind"] == "blocked" and payload["code"] == CODE
        assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None


def test_a_successful_retry_still_goes_through_the_unchanged_gate(monkeypatch):
    """(PR-B 4e) 재검수가 통과해도 발행은 게이트가 정한다 — 이미지가 없으면 이미지로, 금지 표현이
    들어오면 금지 표현으로 막힌다. 통과한 재검수는 계수를 지운다."""

    philosophy = _approved_philosophy()
    item = _stored_post(philosophy, image=False)
    calls = _arm_sweep(monkeypatch, philosophy, _review(ContentAiReviewStatus.PASS))
    monkeypatch.setattr(
        tasks,
        "_recover_missing_content_image",
        lambda *_a: tasks.GenerationItemState.PARTIAL,  # 이미지 공급자는 이번에도 실패했다
    )
    _remember(monkeypatch, item, _kst(SLOT - timedelta(days=1), 12))

    _run_sweeps(monkeypatch, item, [_kst(SLOT, 1)])

    assert len(calls) == 1
    assert item.essence_check_summary["ai_review"]["status"] == "PASS"
    assert "review_unavailable_total" not in _attempt(item)
    payload = _publish_once(monkeypatch, item, philosophy, _kst(SLOT, 8))
    assert payload["kind"] == "blocked" and payload["code"] == "CONTENT_IMAGE_NOT_READY"
    assert item.status is tasks.ContentStatus.DRAFT

    item.meta_description = "완치를 약속드립니다."
    payload = _publish_once(monkeypatch, item, philosophy, _kst(SLOT, 9))
    assert payload["kind"] == "blocked" and payload["code"] == "FORBIDDEN_EXPRESSION"
    assert item.status is tasks.ContentStatus.DRAFT and item.published_at is None
