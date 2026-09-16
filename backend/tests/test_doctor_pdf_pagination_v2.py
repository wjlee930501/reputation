"""Complete appendices, legacy artifacts and byte-bound validation."""

import io
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from monthly_artifact_test_support import published
from pypdf import PdfReader
from test_doctor_report_view import _attribution, _question_row, _view

from app.services.report_artifact_validation import (
    DoctorPdfExpectation,
    DoctorPdfValidationError,
    parse_doctor_artifact_metadata,
    render_validated_doctor_pdf,
    validate_doctor_pdf,
)

CAVEAT = "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다."


def expectation(view):
    return DoctorPdfExpectation(
        hospital_name=view["hospital_name"],
        coverage_text=view["coverage_text"],
        caveat_text=CAVEAT,
        public_url="https://example.test/clinic",
        appendix_expected=bool(view["appendix_rows"]),
    )


@pytest.mark.parametrize(
    "version,pages,valid",
    [
        ("doctor-pdf-v1", 2, True),
        ("doctor-pdf-v1", 3, False),
        ("doctor-pdf-v2", 3, True),
        ("doctor-pdf-v2", 33, False),
        ("doctor-pdf-v2", "3", False),
    ],
)
def test_metadata_versions_preserve_the_legacy_contract(version, pages, valid):
    data = published(uuid4()).metadata.model_dump(mode="json")
    data.update(validation_version=version, page_count=pages)
    assert (parse_doctor_artifact_metadata(data) is not None) is valid


@pytest.mark.skipif(os.getenv("REQUIRE_PDF_RENDER") is None, reason="Native PDF stack required")
@pytest.mark.parametrize("count", [0, 31, 70])
def test_customer_pdf_contains_every_question_in_order(count):
    questions = [
        f"검증용 질문 {index:03d}번의 준비 조건과 주의사항은 무엇인가요?" for index in range(count)
    ]
    view = _view(
        hospital=SimpleNamespace(name="리포트 검증용 의원"),
        records=[],
        attribution=_attribution(
            question_rows=[
                _question_row(str(i), text, current=(10, 0)) for i, text in enumerate(questions)
            ]
        ),
    )
    before = deepcopy(view)
    expected = expectation(view)
    result = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
    )
    assert view == before
    reader = PdfReader(io.BytesIO(result.pdf_bytes))
    assert result.metadata.validation_version == "doctor-pdf-v2"
    assert len(reader.pages) == (1 if not count else result.metadata.page_count)
    appendix = "".join("".join(page.extract_text().split()) for page in reader.pages[1:])
    for page in reader.pages[1:]:
        assert view["hospital_name"].replace(" ", "") in "".join(page.extract_text().split())
    positions = [appendix.index("".join(question.split())) for question in questions]
    assert positions == sorted(positions)
    assert appendix.count("10번중0번") == count
    assert (len(reader.pages) > 2) if count else (len(reader.pages) == 1)
    if evidence_root := os.getenv("PDF_EVIDENCE_DIR"):
        root = Path(evidence_root)
        root.mkdir(parents=True, exist_ok=True)
        (root / f"complete-questions-{count}.pdf").write_bytes(result.pdf_bytes)


@pytest.mark.skipif(os.getenv("REQUIRE_PDF_RENDER") is None, reason="Native PDF stack required")
def test_binary_validation_rejects_missing_duplicate_row_and_wrong_period():
    view = _view(
        records=[], attribution=_attribution(question_rows=[_question_row("q", "검증용 질문")])
    )
    expected = expectation(view)
    result = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
    )
    row = view["appendix_rows"][0]
    expected_row = tuple(
        row[key]
        for key in ("query_text", "prev_label", "current_label", "competitor", "cited_title")
    )
    with pytest.raises(DoctorPdfValidationError, match="질문표"):
        validate_doctor_pdf(
            result.pdf_bytes, replace(expected, required_appendix_rows=(expected_row, expected_row))
        )
    with pytest.raises(DoctorPdfValidationError) as failure:
        validate_doctor_pdf(result.pdf_bytes, replace(expected, period_label="2026-09"))
    assert failure.value.code == "DOCTOR_PDF_REQUIRED_TEXT_MISSING"


def test_oversized_question_table_fails_explicitly_instead_of_truncating():
    view = _view(
        records=[],
        attribution=_attribution(
            question_rows=[_question_row(str(i), f"검증 질문 {i}") for i in range(513)]
        ),
    )
    expected = expectation(view)
    with pytest.raises(DoctorPdfValidationError) as failure:
        render_validated_doctor_pdf(
            view=view, period_label="2026-08", public_url=expected.public_url, expectation=expected
        )
    assert failure.value.code == "DOCTOR_PDF_LAYOUT_LIMIT"
    assert len(view["appendix_rows"]) == 513


def test_spanning_repeat_header_cannot_reset_table_column_widths():
    """Fixed table layout reads colgroup before the first spanning header row."""
    from test_doctor_report_view import _render
    html = _render(_view(records=[], attribution=_attribution(question_rows=[_question_row("q", "검증 질문")])))
    group = html.split("<colgroup>", 1)[1].split("</colgroup>", 1)[0]
    assert group.count("<col ") == 5
    for percentage in (38, 12, 16, 22):
        assert f'width:{percentage}%' in group
    assert html.index("<colgroup>") < html.index('class="table-context"')
