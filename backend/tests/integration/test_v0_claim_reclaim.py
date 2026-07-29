"""V0 ANALYZING 클레임의 생존 판정 — 실제 SQL로 확인한다.

배경: trigger_v0_report는 `hospital.status = ANALYZING`으로 클레임하고 커밋한다.
정상 실패는 _reset_v0_analyzing_status가 되돌리지만 **하드 종료(SIGKILL·OOM·
Cloud Run scale-in)에서는 except가 실행되지 않는다.** 그러면 재배달된 실행이
ANALYZING을 보고 조용히 return하고, v0_report_done은 영원히 False로 남는다.
STEP4(콘텐츠 허브 준비)도 같은 태스크에서 큐잉되므로 함께 멈추고 Slack 신호는 0건이다.

상태 컬럼만으로는 "진행 중"과 "죽은 채 방치됨"을 구분할 수 없어, 측정 실행을
하트비트로 쓴다. 그 판정이 실제 SQL에서 맞는지가 이 테스트의 대상이다.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.workers.tasks import V0_CLAIM_MAX_AGE_SECONDS, _v0_claim_is_alive


def _seed_hospital(conn) -> uuid.UUID:
    hospital_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO hospitals (id, name, slug, status) "
            "VALUES (:id, '통합테스트병원', :slug, 'ANALYZING')"
        ),
        {"id": hospital_id, "slug": f"itest-{uuid.uuid4().hex[:8]}"},
    )
    return hospital_id


def _seed_run(conn, hospital_id, *, status: str, age_seconds: int) -> None:
    started = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    conn.execute(
        text(
            "INSERT INTO measurement_runs (id, hospital_id, run_label, status, started_at) "
            "VALUES (:id, :h, 'V0 first measurement', :s, :t)"
        ),
        {"id": uuid.uuid4(), "h": hospital_id, "s": status, "t": started},
    )


def test_a_recent_running_measurement_means_the_claim_is_alive(pg_conn):
    hospital_id = _seed_hospital(pg_conn)
    _seed_run(pg_conn, hospital_id, status="RUNNING", age_seconds=60)

    with Session(bind=pg_conn, join_transaction_mode="create_savepoint") as session:
        assert _v0_claim_is_alive(session, hospital_id) is True


def test_no_measurement_run_at_all_means_the_claim_is_dead(pg_conn):
    """클레임 커밋 직후 죽으면 측정 실행이 아예 없다 — 이게 영구 고착의 실제 형태다."""
    hospital_id = _seed_hospital(pg_conn)

    with Session(bind=pg_conn, join_transaction_mode="create_savepoint") as session:
        assert _v0_claim_is_alive(session, hospital_id) is False


def test_a_running_measurement_older_than_the_hard_limit_is_dead(pg_conn):
    """하드 리밋을 넘긴 RUNNING은 되돌려지지 못하고 죽은 실행이다."""
    hospital_id = _seed_hospital(pg_conn)
    _seed_run(
        pg_conn, hospital_id, status="RUNNING", age_seconds=V0_CLAIM_MAX_AGE_SECONDS + 60
    )

    with Session(bind=pg_conn, join_transaction_mode="create_savepoint") as session:
        assert _v0_claim_is_alive(session, hospital_id) is False


def test_a_completed_measurement_does_not_keep_the_claim_alive(pg_conn):
    """측정은 끝났는데 PDF 단계에서 죽은 경우도 재트리거가 가능해야 한다."""
    hospital_id = _seed_hospital(pg_conn)
    _seed_run(pg_conn, hospital_id, status="COMPLETED", age_seconds=30)

    with Session(bind=pg_conn, join_transaction_mode="create_savepoint") as session:
        assert _v0_claim_is_alive(session, hospital_id) is False


def test_another_hospitals_running_measurement_does_not_leak(pg_conn):
    hospital_id = _seed_hospital(pg_conn)
    other_id = _seed_hospital(pg_conn)
    _seed_run(pg_conn, other_id, status="RUNNING", age_seconds=30)

    with Session(bind=pg_conn, join_transaction_mode="create_savepoint") as session:
        assert _v0_claim_is_alive(session, hospital_id) is False
