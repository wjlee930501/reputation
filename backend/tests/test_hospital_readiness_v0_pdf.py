"""A-7 — 초기 진단 단계는 리포트 행이 아니라 리포트 PDF를 요구한다.

측정만 끝나고 PDF 생성이 실패한 병원은 원장에게 보여줄 파일이 없다. 그런데도
`v0_report` 준비도 검사가 통과하면 온보딩 3단계가 완료로 표시되어, AE는 없는
리포트를 보고하러 간다. 검사가 실제 PDF 존재를 보는지 여기서 고정한다.
"""

import uuid
from types import SimpleNamespace

from app.api.admin import hospitals as hospitals_api
from app.models.hospital import Hospital, HospitalStatus


class _ReadinessDB:
    """`_count` 호출을 쿼리 대상별로 갈라 주는 최소 세션.

    호출 순서가 아니라 컴파일된 SQL을 보고 값을 고르므로, 핸들러 안에서 카운트
    순서가 바뀌어도 테스트가 조용히 다른 값을 검증하지 않는다.
    """

    def __init__(self, *, report_count: int, report_pdf_count: int) -> None:
        self.report_count = report_count
        self.report_pdf_count = report_pdf_count
        self.hospital: Hospital | None = None

    async def get(self, _model, _pk):
        return self.hospital

    async def execute(self, stmt):
        sql = str(stmt)
        if "monthly_reports" in sql:
            value = self.report_pdf_count if "pdf_path" in sql else self.report_count
        elif "sov_records" in sql:
            value = 1
        elif "content_items" in sql:
            value = 0 if "essence_status" in sql else 1
        else:  # pragma: no cover - 새 카운트가 추가되면 여기서 드러나야 한다
            raise AssertionError(f"unexpected readiness query: {sql}")
        return SimpleNamespace(scalar_one=lambda: value)


def _hospital() -> Hospital:
    return Hospital(
        id=uuid.uuid4(),
        name="장편한외과의원",
        slug="janpyeonhan",
        status=HospitalStatus.ACTIVE,
        v0_report_done=True,
    )


def _fake_essence():
    return SimpleNamespace(
        approved=SimpleNamespace(id=uuid.uuid4(), version=1),
        is_fresh=True,
        current=None,
        processed_source_count=1,
        required_source_count=1,
        has_unprocessed_sources=False,
    )


async def _readiness(monkeypatch, *, report_count: int, report_pdf_count: int) -> dict:
    db = _ReadinessDB(report_count=report_count, report_pdf_count=report_pdf_count)
    db.hospital = _hospital()

    async def fake_essence_readiness(_db, _hospital_id):
        return _fake_essence()

    monkeypatch.setattr(hospitals_api, "get_essence_readiness", fake_essence_readiness)
    return await hospitals_api.get_readiness(db.hospital.id, db=db)


def _check(payload: dict, key: str) -> dict:
    return next(check for check in payload["checks"] if check["key"] == key)


async def test_v0_report_check_fails_when_the_report_row_has_no_pdf(monkeypatch):
    payload = await _readiness(monkeypatch, report_count=1, report_pdf_count=0)

    assert _check(payload, "v0_report")["passed"] is False
    assert payload["report_count"] == 1
    assert payload["report_pdf_count"] == 0
    assert payload["status"] == "NEEDS_WORK"


async def test_v0_report_check_passes_once_a_report_pdf_exists(monkeypatch):
    payload = await _readiness(monkeypatch, report_count=1, report_pdf_count=1)

    assert _check(payload, "v0_report")["passed"] is True
    assert payload["report_pdf_count"] == 1


async def test_the_v0_done_flag_alone_no_longer_passes_the_check(monkeypatch):
    """플래그는 워커가 세우지만 PDF 생성 실패와 무관하게 남는다 — 플래그만으로는 부족하다."""
    payload = await _readiness(monkeypatch, report_count=0, report_pdf_count=0)

    assert _check(payload, "v0_report")["passed"] is False
    assert payload["report_pdf_count"] == 0
