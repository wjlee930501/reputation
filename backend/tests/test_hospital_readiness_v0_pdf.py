"""A-7 — 온보딩 3단계는 "초기 진단(V0) 리포트 + PDF"를 요구한다.

두 가지가 각각 사실과 어긋날 수 있다.

  * 측정만 끝나고 PDF 생성이 실패하면 리포트 행은 남지만 원장에게 보여줄 파일이 없다.
  * 월간 리포트 PDF는 초기 진단이 아니다. 종류를 가리지 않고 세면 운영 몇 달째 병원이
    월간 PDF 덕에 초기 진단을 건너뛴 채로 완료 표시된다.

여기서는 카운트가 검사·응답으로 이어지는 배선과, 그 카운트 쿼리가 실제로 V0로
걸러지는지를 고정한다. 실제 행을 넣고 확인하는 것은
`tests/integration/test_readiness_v0_report_pdf_postgres.py`가 담당한다.
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

    def __init__(self, *, report_count: int, v0_report_pdf_count: int) -> None:
        self.report_count = report_count
        self.v0_report_pdf_count = v0_report_pdf_count
        self.hospital: Hospital | None = None
        self.report_sql: list[str] = []

    async def get(self, _model, _pk):
        return self.hospital

    async def execute(self, stmt):
        sql = str(stmt)
        if "monthly_reports" in sql:
            literal = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            self.report_sql.append(literal)
            value = self.v0_report_pdf_count if "pdf_path" in sql else self.report_count
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


async def _run(monkeypatch, *, report_count: int, v0_report_pdf_count: int) -> tuple[dict, _ReadinessDB]:
    db = _ReadinessDB(report_count=report_count, v0_report_pdf_count=v0_report_pdf_count)
    db.hospital = _hospital()

    async def fake_essence_readiness(_db, _hospital_id):
        return _fake_essence()

    monkeypatch.setattr(hospitals_api, "get_essence_readiness", fake_essence_readiness)
    return await hospitals_api.get_readiness(db.hospital.id, db=db), db


async def _readiness(monkeypatch, *, report_count: int, v0_report_pdf_count: int) -> dict:
    payload, _ = await _run(
        monkeypatch, report_count=report_count, v0_report_pdf_count=v0_report_pdf_count
    )
    return payload


def _check(payload: dict, key: str) -> dict:
    return next(check for check in payload["checks"] if check["key"] == key)


async def test_v0_report_check_fails_when_the_report_row_has_no_pdf(monkeypatch):
    payload = await _readiness(monkeypatch, report_count=1, v0_report_pdf_count=0)

    assert _check(payload, "v0_report")["passed"] is False
    assert payload["report_count"] == 1
    assert payload["v0_report_pdf_count"] == 0
    assert payload["status"] == "NEEDS_WORK"


async def test_v0_report_check_passes_once_an_initial_diagnosis_pdf_exists(monkeypatch):
    payload = await _readiness(monkeypatch, report_count=1, v0_report_pdf_count=1)

    assert _check(payload, "v0_report")["passed"] is True
    assert payload["v0_report_pdf_count"] == 1


async def test_the_v0_done_flag_alone_no_longer_passes_the_check(monkeypatch):
    """플래그는 워커가 세우지만 PDF 생성 실패와 무관하게 남는다 — 플래그만으로는 부족하다."""
    payload = await _readiness(monkeypatch, report_count=0, v0_report_pdf_count=0)

    assert _check(payload, "v0_report")["passed"] is False
    assert payload["v0_report_pdf_count"] == 0


async def test_the_pdf_count_query_is_restricted_to_initial_diagnosis_reports(monkeypatch):
    """월간 PDF가 초기 진단을 대신하지 못하도록 쿼리 자체가 V0로 걸러져야 한다."""
    _, db = await _run(monkeypatch, report_count=1, v0_report_pdf_count=1)

    pdf_queries = [sql for sql in db.report_sql if "pdf_path" in sql]
    assert len(pdf_queries) == 1
    pdf_query = pdf_queries[0]
    # 등호까지 확인한다 — 부등호로 뒤집히면(`report_type != 'V0'`) 초기 진단 대신
    # 월간 PDF만 세게 된다. (`pdf_path != ''`는 정상이므로 report_type만 본다.)
    assert "report_type = 'V0'" in pdf_query
    assert "report_type !=" not in pdf_query

    # 전체 리포트 수는 종류를 가리지 않는다 — 그 값으로 완료를 판정하지 않을 뿐이다.
    plain_queries = [sql for sql in db.report_sql if "pdf_path" not in sql]
    assert len(plain_queries) == 1
    assert "report_type" not in plain_queries[0]
