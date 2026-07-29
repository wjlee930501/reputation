"""무료 진단의 핵심 제약을 **실제 SQL로** 검증한다 (설계 T-1 · T-5 · T-6 · T-11).

이 파일이 검증하는 것은 전부 Postgres 전용 구문(부분 유니크 인덱스, CHECK 제약)이라
mock 기반 단위 스위트로는 도달할 수 없다. 그런데 이것들이 퍼널의 뼈대다 —
잠금이 안 걸리면 하루 20건 상한이 무의미해지고, 축 간 CHECK가 없으면 측정 실패한
진단으로 리포트가 만들어져 원장에게 간다.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _insert_lead(conn, *, source="AI_DIAGNOSIS"):
    lead_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO sales_leads (id, clinic_name, clinic_type, contact, privacy, source) "
            "VALUES (:id, '통합테스트의원', '내과', '010-0000-0000', true, :source)"
        ),
        {"id": lead_id, "source": source},
    )
    return lead_id


def _insert_diagnosis(
    conn,
    *,
    lead_id,
    email_hash=None,
    phone_hash=None,
    slot_date=None,
    slot_no=1,
    execution_status="PENDING",
    report_status="PENDING",
    delivery_status="PENDING",
    lock_released_at=None,
):
    diagnosis_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO lead_diagnoses ("
            "  id, lead_id, applicant_email_hash, subject_phone_hash,"
            "  subject_hospital_name, subject_region, slot_date, slot_no,"
            "  queries, requested_models, repeat_count,"
            "  execution_status, report_status, delivery_status, lock_released_at"
            ") VALUES ("
            "  :id, :lead_id, :email_hash, :phone_hash,"
            "  '통합테스트의원', '수서역', :slot_date, :slot_no,"
            "  '[]'::jsonb, '{}'::jsonb, 3,"
            "  :execution_status, :report_status, :delivery_status, :lock_released_at"
            ")"
        ),
        {
            "id": diagnosis_id,
            "lead_id": lead_id,
            "email_hash": email_hash or uuid.uuid4().hex,
            "phone_hash": phone_hash or uuid.uuid4().hex,
            "slot_date": slot_date or date(2026, 8, 1),
            "slot_no": slot_no,
            "execution_status": execution_status,
            "report_status": report_status,
            "delivery_status": delivery_status,
            "lock_released_at": lock_released_at,
        },
    )
    return diagnosis_id


class TestDualLock:
    """전화번호와 이메일을 **각각** 잠근다 (설계 §2-3).

    한쪽만 걸면 우회가 남는다 — 전화번호만 잠그면 메일만 바꿔 남의 병원을 계속
    신청할 수 있고, 이메일만 잠그면 한 사람이 여러 병원을 훑을 수 있다.
    """

    def test_same_email_with_a_different_phone_is_rejected(self, pg_conn):
        lead = _insert_lead(pg_conn)
        shared_email = uuid.uuid4().hex
        _insert_diagnosis(pg_conn, lead_id=lead, email_hash=shared_email, slot_no=1)
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(pg_conn, lead_id=lead, email_hash=shared_email, slot_no=2)

    def test_same_phone_with_a_different_email_is_rejected(self, pg_conn):
        lead = _insert_lead(pg_conn)
        shared_phone = uuid.uuid4().hex
        _insert_diagnosis(pg_conn, lead_id=lead, phone_hash=shared_phone, slot_no=1)
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(pg_conn, lead_id=lead, phone_hash=shared_phone, slot_no=2)

    def test_both_new_is_accepted(self, pg_conn):
        lead = _insert_lead(pg_conn)
        _insert_diagnosis(pg_conn, lead_id=lead, slot_no=1)
        _insert_diagnosis(pg_conn, lead_id=lead, slot_no=2)

    def test_released_lock_frees_both_keys(self, pg_conn):
        """AE 해제가 없으면 이 잠금은 리드 차단 장치가 된다 (설계 §2-4).

        제3자가 먼저 신청해 원장의 기회를 소진시킨 경우를 푸는 유일한 경로다.
        """
        lead = _insert_lead(pg_conn)
        email_hash = uuid.uuid4().hex
        phone_hash = uuid.uuid4().hex
        first = _insert_diagnosis(
            pg_conn, lead_id=lead, email_hash=email_hash, phone_hash=phone_hash, slot_no=1
        )

        # 해제 전에는 막힌다.
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(
                    pg_conn,
                    lead_id=lead,
                    email_hash=email_hash,
                    phone_hash=phone_hash,
                    slot_no=2,
                )

        pg_conn.execute(
            text(
                "UPDATE lead_diagnoses SET lock_released_at = now(), lock_released_by = 'ae@x' "
                "WHERE id = :id"
            ),
            {"id": first},
        )

        # 해제 후에는 같은 번호·메일로 다시 신청할 수 있다.
        _insert_diagnosis(
            pg_conn, lead_id=lead, email_hash=email_hash, phone_hash=phone_hash, slot_no=2
        )

    def test_release_does_not_free_other_diagnoses(self, pg_conn):
        """해제는 그 행에만 적용된다 — 한 건을 풀어 전체 잠금이 열리면 안 된다."""
        lead = _insert_lead(pg_conn)
        kept_email = uuid.uuid4().hex
        _insert_diagnosis(pg_conn, lead_id=lead, email_hash=kept_email, slot_no=1)
        released = _insert_diagnosis(pg_conn, lead_id=lead, slot_no=2)
        pg_conn.execute(
            text("UPDATE lead_diagnoses SET lock_released_at = now() WHERE id = :id"),
            {"id": released},
        )
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(pg_conn, lead_id=lead, email_hash=kept_email, slot_no=3)


class TestDailySlots:
    """선착순 자리는 DB가 배정한다 (설계 §2-1).

    Redis 카운터를 쓰면 카운터와 실제 행 수가 어긋날 수 있다. 여기서는 그 어긋남이
    구조적으로 불가능함을 확인한다.
    """

    def test_same_slot_number_on_the_same_day_is_rejected(self, pg_conn):
        lead = _insert_lead(pg_conn)
        day = date(2026, 8, 2)
        _insert_diagnosis(pg_conn, lead_id=lead, slot_date=day, slot_no=7)
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(pg_conn, lead_id=lead, slot_date=day, slot_no=7)

    def test_the_same_slot_number_reopens_the_next_day(self, pg_conn):
        """자리는 KST 자정에 리셋된다 — 어제 7번이 오늘 7번을 막으면 안 된다."""
        lead = _insert_lead(pg_conn)
        _insert_diagnosis(pg_conn, lead_id=lead, slot_date=date(2026, 8, 2), slot_no=7)
        _insert_diagnosis(pg_conn, lead_id=lead, slot_date=date(2026, 8, 3), slot_no=7)

    def test_slot_number_must_be_positive(self, pg_conn):
        lead = _insert_lead(pg_conn)
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(pg_conn, lead_id=lead, slot_no=0)

    def test_the_counter_serializes_allocation_up_to_the_limit(self, pg_conn):
        """접수 API가 **실제로 쓰는** 원자적 배정 SQL.

        예전 테스트는 `MAX(slot_no)+1`을 "접수 API가 쓰는 바로 그 SQL"이라고 적어놨는데
        API는 그것을 쓰지 않았다 — 테스트가 검증한다고 주장한 것과 코드가 하는 일이
        달랐고, 그래서 동시 접수가 무너지는 것을 아무도 못 봤다.
        """
        day = date(2026, 8, 5)
        pg_conn.execute(
            text("INSERT INTO lead_diagnosis_slot_days (slot_date, used) VALUES (:d, 0)"),
            {"d": day},
        )
        claim = text(
            "UPDATE lead_diagnosis_slot_days SET used = used + 1 "
            "WHERE slot_date = :d AND used < :limit RETURNING used"
        )
        for expected in (1, 2, 3):
            assert pg_conn.execute(claim, {"d": day, "limit": 3}).scalar_one() == expected
        # 한도에 닿으면 더 이상 배정되지 않는다 — 이것이 예산 상한의 전부다.
        assert pg_conn.execute(claim, {"d": day, "limit": 3}).scalar_one_or_none() is None

    def test_the_counter_cannot_go_negative(self, pg_conn):
        day = date(2026, 8, 6)
        pg_conn.execute(
            text("INSERT INTO lead_diagnosis_slot_days (slot_date, used) VALUES (:d, 0)"),
            {"d": day},
        )
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                pg_conn.execute(
                    text("UPDATE lead_diagnosis_slot_days SET used = -1 WHERE slot_date = :d"),
                    {"d": day},
                )


class TestAxisInvariants:
    """축 간 진입 조건 (설계 §4-3).

    "측정 없이 리포트", "리포트 없이 발송"이 스키마 수준에서 불가능해야 한다.
    이것이 없으면 측정이 통째로 실패한 진단으로 리포트가 만들어져 원장에게 간다.
    """

    @pytest.mark.parametrize("execution_status", ["PENDING", "RUNNING", "FAILED"])
    @pytest.mark.parametrize("report_status", ["BUILDING", "READY"])
    def test_a_report_cannot_be_built_without_a_usable_execution(
        self, pg_conn, execution_status, report_status
    ):
        lead = _insert_lead(pg_conn)
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(
                    pg_conn,
                    lead_id=lead,
                    execution_status=execution_status,
                    report_status=report_status,
                )

    @pytest.mark.parametrize("report_status", ["BLOCKED", "PURGED"])
    def test_terminal_report_states_do_not_require_a_usable_execution(
        self, pg_conn, report_status
    ):
        """BLOCKED는 실행 실패 시 도달하는 정상 상태이고, PURGED는 파기의 종결이다.

        이 둘을 막으면 각각 "리포트 차단"과 "파기"가 프로덕션에서 크래시한다 —
        후자는 그날 만료된 모든 리드의 파기를 함께 롤백시킨다.
        """
        lead = _insert_lead(pg_conn)
        _insert_diagnosis(
            pg_conn, lead_id=lead, execution_status="FAILED", report_status=report_status
        )

    @pytest.mark.parametrize("execution_status", ["SUCCEEDED", "PARTIAL"])
    def test_report_may_proceed_on_succeeded_or_partial(self, pg_conn, execution_status):
        """PARTIAL로도 리포트를 만든다 — 두 플랫폼 모두 데이터가 있다는 뜻이기 때문이다.

        어느 한 플랫폼이라도 성공 0이면 상태가 FAILED이지 PARTIAL이 아니다(설계 §4-4).
        """
        lead = _insert_lead(pg_conn)
        _insert_diagnosis(
            pg_conn, lead_id=lead, execution_status=execution_status, report_status="READY"
        )

    def test_delivery_cannot_start_before_the_report_is_ready(self, pg_conn):
        lead = _insert_lead(pg_conn)
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                _insert_diagnosis(
                    pg_conn,
                    lead_id=lead,
                    execution_status="SUCCEEDED",
                    report_status="BUILDING",
                    delivery_status="SENDING",
                )

    def test_delivery_may_proceed_after_purge(self, pg_conn):
        """재발송 시도가 파기 뒤에 들어올 수 있다 — 그때는 410을 주면 된다."""
        lead = _insert_lead(pg_conn)
        _insert_diagnosis(
            pg_conn,
            lead_id=lead,
            execution_status="SUCCEEDED",
            report_status="PURGED",
            delivery_status="FAILED",
        )


class TestMeasurementUniqueness:
    def test_the_same_measurement_cannot_be_recorded_twice(self, pg_conn):
        """태스크 재배달로 같은 측정이 두 번 기록되면 분모가 부풀어 언급률이 낮아진다."""
        lead = _insert_lead(pg_conn)
        diagnosis = _insert_diagnosis(pg_conn, lead_id=lead)
        params = {
            "diagnosis_id": diagnosis,
            "measured_at": datetime.now(timezone.utc),
        }
        insert = text(
            "INSERT INTO lead_diagnosis_results ("
            "  id, diagnosis_id, platform, query_slot, repeat_no, attempt_no,"
            "  query_text, requested_model, measurement_status, measured_at"
            ") VALUES ("
            "  :id, :diagnosis_id, 'chatgpt', 1, 1, 1,"
            "  '수서역 근처 내과 병원 추천해줘', 'gpt-5.6-luna', 'SUCCESS', :measured_at"
            ")"
        )
        pg_conn.execute(insert, {"id": uuid.uuid4(), **params})
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                pg_conn.execute(insert, {"id": uuid.uuid4(), **params})

    def test_a_retry_attempt_is_a_separate_row(self, pg_conn):
        """재시도는 덮어쓰지 않고 새 행이다 — 실패 이력이 사라지면 원가 검증이 불가능하다."""
        lead = _insert_lead(pg_conn)
        diagnosis = _insert_diagnosis(pg_conn, lead_id=lead)
        insert = text(
            "INSERT INTO lead_diagnosis_results ("
            "  id, diagnosis_id, platform, query_slot, repeat_no, attempt_no,"
            "  query_text, requested_model, measurement_status, measured_at"
            ") VALUES ("
            "  :id, :diagnosis_id, 'chatgpt', 1, 1, :attempt_no,"
            "  'q', 'gpt-5.6-luna', 'SUCCESS', now()"
            ")"
        )
        for attempt in (1, 2):
            pg_conn.execute(
                insert,
                {"id": uuid.uuid4(), "diagnosis_id": diagnosis, "attempt_no": attempt},
            )


class TestQueryAnswerCache:
    """질의 단위 공유 캐시 (설계 §2-6) — 원가를 300배 줄이는 지점."""

    def _insert_answer(self, conn, *, query_hash, repeat_no=1, expires_in_days=7):
        conn.execute(
            text(
                "INSERT INTO lead_query_answers ("
                "  id, query_hash, repeat_no, query_text, platform, requested_model,"
                "  prompt_version, raw_response, measured_at, expires_at"
                ") VALUES ("
                "  :id, :query_hash, :repeat_no, 'q', 'chatgpt', 'gpt-5.6-luna',"
                "  'v1', 'answer', now(), :expires_at"
                ")"
            ),
            {
                "id": uuid.uuid4(),
                "query_hash": query_hash,
                "repeat_no": repeat_no,
                "expires_at": datetime.now(timezone.utc) + timedelta(days=expires_in_days),
            },
        )

    def test_one_answer_per_query_and_repeat(self, pg_conn):
        query_hash = uuid.uuid4().hex
        self._insert_answer(pg_conn, query_hash=query_hash, repeat_no=1)
        with pytest.raises(IntegrityError):
            with pg_conn.begin_nested():
                self._insert_answer(pg_conn, query_hash=query_hash, repeat_no=1)

    def test_repeats_of_the_same_query_are_stored_separately(self, pg_conn):
        """반복 3회가 'N번 중 M번'의 근거다 — 회차별로 다른 답변을 각각 남긴다."""
        query_hash = uuid.uuid4().hex
        for repeat_no in (1, 2, 3):
            self._insert_answer(pg_conn, query_hash=query_hash, repeat_no=repeat_no)


class TestCascadeFromLead:
    def test_deleting_a_lead_removes_its_diagnosis_and_results(self, pg_conn):
        """파기 경로가 진단 산출물을 남기고 가면 파기가 거짓말이 된다."""
        lead = _insert_lead(pg_conn)
        diagnosis = _insert_diagnosis(pg_conn, lead_id=lead)
        pg_conn.execute(
            text(
                "INSERT INTO lead_diagnosis_results ("
                "  id, diagnosis_id, platform, query_slot, repeat_no, attempt_no,"
                "  query_text, requested_model, measurement_status, measured_at"
                ") VALUES (:id, :diagnosis_id, 'gemini', 1, 1, 1, 'q', 'm', 'SUCCESS', now())"
            ),
            {"id": uuid.uuid4(), "diagnosis_id": diagnosis},
        )

        pg_conn.execute(text("DELETE FROM sales_leads WHERE id = :id"), {"id": lead})

        remaining = pg_conn.execute(
            text("SELECT count(*) FROM lead_diagnosis_results WHERE diagnosis_id = :id"),
            {"id": diagnosis},
        ).scalar_one()
        assert remaining == 0
