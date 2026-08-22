"""A-7 — 실제 행으로 확인한다: 월간 PDF는 초기 진단 단계를 완료시키지 못한다.

카운트를 손으로 넣는 단위 테스트는 프로덕션 필터가 뒤집혀도(예: MONTHLY로 걸러도)
통과할 수 있다. 여기서는 진짜 리포트 행을 넣고 실제 SQL이 도는 결과를 본다.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.api.admin import hospitals as hospitals_api
from app.models.hospital import Hospital, HospitalStatus
from app.models.report import MONTHLY_REPORT_TYPE, V0_REPORT_TYPE, MonthlyReport


@pytest.fixture
def hospital():
    return Hospital(
        id=uuid.uuid4(),
        name="장편한외과의원",
        slug=f"readiness-v0-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ACTIVE,
        # 워커가 세우는 플래그. PDF 생성 실패와 무관하게 남으므로 이것만으로는 부족하다.
        v0_report_done=True,
    )


def _report(hospital_id: uuid.UUID, *, report_type: str, pdf_path: str | None, month: int):
    return MonthlyReport(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        period_year=2026,
        period_month=month,
        report_type=report_type,
        pdf_path=pdf_path,
        sov_summary={"overall": 50.0},
        created_at=datetime.now(timezone.utc),
    )


async def _readiness(session, hospital, monkeypatch) -> dict:
    async def fake_essence_readiness(_db, _hospital_id):
        from types import SimpleNamespace

        return SimpleNamespace(
            approved=SimpleNamespace(id=uuid.uuid4(), version=1),
            is_fresh=True,
            current=None,
            processed_source_count=1,
            required_source_count=1,
            has_unprocessed_sources=False,
        )

    monkeypatch.setattr(hospitals_api, "get_essence_readiness", fake_essence_readiness)
    return await hospitals_api.get_readiness(hospital.id, db=session)


def _v0_check(payload: dict) -> dict:
    return next(check for check in payload["checks"] if check["key"] == "v0_report")


async def test_a_monthly_report_pdf_alone_leaves_step_three_incomplete(
    pg_async_session, hospital, monkeypatch
):
    """운영 몇 달째 병원이 월간 PDF 덕에 초기 진단을 건너뛴 채 완료로 보이면 안 된다."""
    pg_async_session.add_all(
        [
            hospital,
            _report(hospital.id, report_type=MONTHLY_REPORT_TYPE, pdf_path="gs://b/2026-07.pdf", month=7),
            _report(hospital.id, report_type=MONTHLY_REPORT_TYPE, pdf_path="gs://b/2026-06.pdf", month=6),
        ]
    )
    await pg_async_session.flush()

    payload = await _readiness(pg_async_session, hospital, monkeypatch)

    assert payload["report_count"] == 2
    assert payload["v0_report_pdf_count"] == 0
    assert _v0_check(payload)["passed"] is False
    assert payload["status"] == "NEEDS_WORK"


async def test_an_initial_diagnosis_pdf_completes_step_three(
    pg_async_session, hospital, monkeypatch
):
    pg_async_session.add_all(
        [
            hospital,
            _report(hospital.id, report_type=V0_REPORT_TYPE, pdf_path="gs://b/v0.pdf", month=8),
        ]
    )
    await pg_async_session.flush()

    payload = await _readiness(pg_async_session, hospital, monkeypatch)

    assert payload["v0_report_pdf_count"] == 1
    assert _v0_check(payload)["passed"] is True


async def test_an_initial_diagnosis_row_without_a_pdf_leaves_step_three_incomplete(
    pg_async_session, hospital, monkeypatch
):
    """측정은 끝났지만 PDF 생성이 실패한 상태 — 원장에게 보여줄 파일이 없다."""
    pg_async_session.add_all(
        [
            hospital,
            _report(hospital.id, report_type=V0_REPORT_TYPE, pdf_path=None, month=8),
        ]
    )
    await pg_async_session.flush()

    payload = await _readiness(pg_async_session, hospital, monkeypatch)

    assert payload["report_count"] == 1
    assert payload["v0_report_pdf_count"] == 0
    assert _v0_check(payload)["passed"] is False


async def test_an_empty_pdf_path_counts_as_no_pdf(pg_async_session, hospital, monkeypatch):
    pg_async_session.add_all(
        [
            hospital,
            _report(hospital.id, report_type=V0_REPORT_TYPE, pdf_path="", month=8),
        ]
    )
    await pg_async_session.flush()

    payload = await _readiness(pg_async_session, hospital, monkeypatch)

    assert payload["v0_report_pdf_count"] == 0
    assert _v0_check(payload)["passed"] is False


async def test_a_monthly_pdf_does_not_mask_a_broken_initial_diagnosis(
    pg_async_session, hospital, monkeypatch
):
    """가장 헷갈리는 조합 — 월간은 PDF가 있고 초기 진단만 실패한 병원."""
    pg_async_session.add_all(
        [
            hospital,
            _report(hospital.id, report_type=V0_REPORT_TYPE, pdf_path=None, month=8),
            _report(hospital.id, report_type=MONTHLY_REPORT_TYPE, pdf_path="gs://b/2026-07.pdf", month=7),
        ]
    )
    await pg_async_session.flush()

    payload = await _readiness(pg_async_session, hospital, monkeypatch)

    assert payload["report_count"] == 2
    assert payload["v0_report_pdf_count"] == 0
    assert _v0_check(payload)["passed"] is False
