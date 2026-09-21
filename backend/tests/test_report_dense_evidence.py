"""Long ordinary report evidence flows to the appendix instead of blocking delivery."""

from dataclasses import replace
from io import BytesIO

from pypdf import PdfReader
from test_report_redesign import monthly_view

from app.services.doctor_pdf_contracts import DoctorPdfExpectation
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf
from app.services.report_narrative import PublishedWork


def test_long_work_titles_and_both_260_character_quotes_are_preserved():
    view = monthly_view(v0_baseline={"of_hundred": 10, "current_of_hundred": 40, "sentence": "참고"})
    question = "서울 지역에서 만성질환 정기 상담과 건강검진을 함께 받으려면 어떤 진료 정보와 검사 준비 사항을 미리 확인해야 하나요?"
    excerpt = ("공식 병원 자료에서 진료 범위와 상담 절차를 확인하고, 내원 전에 기존 검사 결과와 복용 약 정보를 준비할 수 있습니다. " * 4)[:260]
    for key in ("found", "missing"):
        view["evidence"][key] = {
            "question": question, "excerpt": excerpt, "platform": "OpenAI API",
            "measured_at": None, "competitors": [],
        }
    works = tuple(PublishedWork(
        title="만성질환 추적 상담과 건강검진 전 준비 및 검사 결과 상담에 관한 공식 진료 안내",
        content_id=f"fixture-{i}", url=None, cited_cells=1, queries=(question,),
    ) for i in range(2))
    view["narrative"] = replace(view["narrative"], works=works)
    expected = DoctorPdfExpectation(
        view["hospital_name"], view["coverage_text"],
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
        "https://fictional.example.invalid/",
    )
    result = render_validated_doctor_pdf(
        view=view, period_label="2026-08", public_url=expected.public_url,
        expectation=expected,
    )
    pages = PdfReader(BytesIO(result.pdf_bytes)).pages
    assert result.metadata.validation_version == "doctor-pdf-v3"
    assert "활용결과" in "".join(pages[1].extract_text().split())
    appendix = "".join("".join(page.extract_text().split()) for page in pages[3:])
    assert appendix.count("".join(excerpt.split())) == 2
    assert "언급관측" in appendix and "미언급관측" in appendix
