"""리드 진단 측정 실패는 즉시·1·2·4시간 물러서 5회까지 다시 한다(Phase 1-9b)."""

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


def test_a_missing_attempt_count_is_treated_as_the_first():
    assert leadgen.execution_retry_not_before(0, NOW) == NOW
