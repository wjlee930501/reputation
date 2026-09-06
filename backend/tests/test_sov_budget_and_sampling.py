"""비용 가드 예약 단위와 V0 표본 결정성 회귀 테스트.

두 결함 모두 프로덕션에 있었고, 둘 다 조용히 틀린다 — 예외도 로그도 남기지 않는다.

1. 비용 가드가 (질의 × 플랫폼)만 예약하고 반복 횟수를 빼먹어, 실제 호출의 1/5만
   세고 있었다. 상한이 사실상 5배로 열려 있었다.
2. V0 표본 질의 5개를 created_at 동률 시 랜덤 UUID인 id로 정렬해 골랐다. 매트릭스는
   한 트랜잭션에서 삽입돼 created_at이 전부 같으므로, 사실상 무작위 표본이었다.
"""
import uuid
from types import SimpleNamespace

import pytest

from app.models.operations import IncidentSeverity
from app.workers import tasks, weekly_sov_incident_control

# ── 비용 가드 예약 단위 = 실제 공급자 호출 수 ──


def test_budget_units_multiply_by_repeat_count():
    # 질의 5 × 플랫폼 2 × 반복 5 = 50회. 반복을 빼면 10회로 5배 적게 예약된다.
    assert tasks.sov_budget_units(query_count=5, platform_count=2, repeat_count=5) == 50


def test_budget_units_match_actual_call_count():
    """예약 단위가 run_single_query가 실제로 내는 호출 수와 일치해야 한다."""
    queries, platforms, repeats = 5, 2, tasks.V0_REPEAT_COUNT
    # run_single_query는 (질의 × 플랫폼)마다 repeat_count번 호출한다.
    actual_calls = queries * platforms * repeats
    reserved = tasks.sov_budget_units(
        query_count=queries, platform_count=platforms, repeat_count=repeats
    )
    assert reserved == actual_calls


def test_budget_units_never_undercount_when_repeats_grow():
    single = tasks.sov_budget_units(query_count=3, platform_count=2, repeat_count=1)
    many = tasks.sov_budget_units(query_count=3, platform_count=2, repeat_count=10)
    assert many == single * 10


def test_high_priority_capacity_uses_one_digest_key_per_week():
    first = weekly_sov_incident_control._capacity_digest_key("2026-W35")
    same_week_other_hospital = weekly_sov_incident_control._capacity_digest_key("2026-W35")
    next_week = weekly_sov_incident_control._capacity_digest_key("2026-W36")

    assert first == same_week_other_hospital
    assert first != next_week


@pytest.mark.asyncio
async def test_high_priority_capacity_is_named_admin_incident_without_slack(monkeypatch):
    previous = SimpleNamespace(
        safe_error_message=(
            "이번 주 HIGH 우선순위 측정 항목이 안전 상한으로 제외되었습니다. "
            "병원별 제외: 기존의원 2개"
        )
    )
    opened = []

    class _DB:
        committed = False
        locked = False

        async def execute(self, _stmt):
            self.locked = True

        async def scalar(self, _stmt):
            return previous

        async def commit(self):
            self.committed = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    db = _DB()

    class _Sessions:
        def __call__(self):
            return db

    async def fake_open(_db, request, **_kwargs):
        opened.append(request)
        return SimpleNamespace(id=uuid.uuid4())

    async def reject_slack(*_args, **_kwargs):
        raise AssertionError("capacity digest must not enqueue Slack")

    monkeypatch.setattr(
        weekly_sov_incident_control, "get_async_sessionmaker", lambda: _Sessions()
    )
    monkeypatch.setattr(weekly_sov_incident_control, "open_or_touch_incident", fake_open)
    monkeypatch.setattr(weekly_sov_incident_control, "enqueue_notification", reject_slack)

    incident_id = await weekly_sov_incident_control.open_weekly_sov_capacity_digest(
        week_key="2026-W35",
        hospital_name="새로운의원",
        trimmed_count=7,
    )

    assert incident_id is not None
    assert db.committed is True
    assert db.locked is True
    assert len(opened) == 1
    request = opened[0]
    assert request.severity is IncidentSeverity.MEDIUM
    assert "새로운의원 7개" in request.customer_impact
    assert "기존의원 2개" in request.customer_impact
    assert request.safe_error_message == request.customer_impact
    assert "주간 검색 노출 전체 병원" not in request.safe_error_message


def test_weekly_sov_cap_cannot_outspend_daily_content_generation():
    weekly_sov_max_calls = tasks.SOV_TOTAL_SPEC_CAP * tasks.SOV_REPEAT_WEEKLY

    assert weekly_sov_max_calls <= tasks.settings.COST_GUARD_DAILY_CONTENT_CALLS
    assert tasks.settings.COST_GUARD_DAILY_SOV_QUERIES == 1260
    assert tasks.settings.COST_GUARD_MONTHLY_SOV_QUERIES == 4260


# ── V0 표본 질의 결정성 ──


def _order_by_columns(stmt) -> list[str]:
    return [str(c) for c in stmt._order_by_clauses]


def test_v0_sample_orders_by_query_text_not_random_id():
    stmt = tasks.v0_sample_query_stmt(uuid.uuid4())
    cols = " ".join(_order_by_columns(stmt))

    # query_text가 tiebreaker여야 한다.
    assert "query_matrix.query_text" in cols
    # id로 정렬하면 랜덤 UUID가 표본을 정하게 된다 — 재현 불가.
    assert "query_matrix.id" not in cols


def test_v0_sample_keeps_limit_and_hospital_filter():
    hospital_id = uuid.uuid4()
    stmt = tasks.v0_sample_query_stmt(hospital_id)

    assert stmt._limit == tasks.V0_QUERY_SAMPLE_COUNT
    assert "query_matrix.hospital_id" in str(stmt.whereclause)


def test_v0_sample_stmt_is_stable_across_calls():
    """같은 병원이면 언제 호출해도 같은 SELECT여야 한다(무작위 요소 없음)."""
    hospital_id = uuid.uuid4()
    first = str(tasks.v0_sample_query_stmt(hospital_id))
    second = str(tasks.v0_sample_query_stmt(hospital_id))

    assert first == second
