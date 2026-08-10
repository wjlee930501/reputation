"""블러 리포트가 실제로 가리는지 (설계 T-7 · PRD F5-3).

이 파일이 가장 값비싼 테스트다. F5-3이 존재하는 이유가 "CSS blur는 텍스트 레이어에서
복원된다"이고, 그런 종류의 결함은 **문서에 적어두면 지켜질 것**이라는 가정이 깨진
지점이기 때문이다. 여기서는 가정이 아니라 산출물을 본다.

전략: 가릴 대상(경쟁 병원명·답변 원문)을 결과 행에 **일부러 심어두고**, 렌더 결과에
그 문자열이 0회 등장하는지 확인한다.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.lead_diagnosis import LeadDiagnosis, LeadDiagnosisResult
from app.services import lead_report

# 심어둘 비밀 — 렌더 결과 어디에도 나오면 안 되는 문자열.
COMPETITOR_NAME = "경쟁하나내과의원"
RAW_EXCERPT = "수서역 근처로는 경쟁하나내과의원과 경쟁둘의원을 추천드립니다"
SOURCE_URL = "https://competitor.example.com/secret-listing"


def _diagnosis() -> LeadDiagnosis:
    return LeadDiagnosis(
        id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        applicant_email_hash="x",
        subject_phone_hash="y",
        subject_hospital_name="장편한외과의원",
        subject_region="수서역",
        queries=[
            {"slot": 1, "kind": "진료과형", "text": "수서역 근처 외과 병원 추천해줘"},
            {"slot": 2, "kind": "시술형", "text": "수서역 근처 대장내시경 병원 추천해줘"},
            {"slot": 3, "kind": "증상형", "text": "치질이 있는데 수서역 근처 병원 어디로 가야해?"},
        ],
        requested_models={
            "openai": "gpt-5.6-luna",
            "gemini": "gemini-3.6-flash",
            "judge": "gpt-4o-mini-2024-07-18",
        },
        repeat_count=3,
    )


def _results(diagnosis: LeadDiagnosis, *, mentioned_per_platform=2, failed_per_platform=1):
    """가릴 대상을 raw_response·competitor에 심은 측정 결과 18건."""
    rows: list[LeadDiagnosisResult] = []
    base = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)
    for platform in ("chatgpt", "gemini"):
        made_mentioned = 0
        made_failed = 0
        for query in diagnosis.queries:
            for repeat_no in range(1, diagnosis.repeat_count + 1):
                failed = made_failed < failed_per_platform
                mentioned = (not failed) and made_mentioned < mentioned_per_platform
                if failed:
                    made_failed += 1
                elif mentioned:
                    made_mentioned += 1
                rows.append(
                    LeadDiagnosisResult(
                        diagnosis_id=diagnosis.id,
                        platform=platform,
                        query_slot=query["slot"],
                        repeat_no=repeat_no,
                        attempt_no=1,
                        query_text=query["text"],
                        requested_model="m",
                        answer_model="m-actual",
                        is_mentioned=None if failed else mentioned,
                        measurement_status="FAILED" if failed else "SUCCESS",
                        failure_reason="provider_query_failed:TimeoutError" if failed else None,
                        raw_response="" if failed else RAW_EXCERPT,
                        source_urls=[SOURCE_URL],
                        answer_source="LIVE",
                        measured_at=base + timedelta(minutes=repeat_no),
                    )
                )
    return rows


@pytest.fixture
def payload():
    diagnosis = _diagnosis()
    return lead_report.build_lead_report_payload(
        diagnosis, _results(diagnosis), generated_at=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    )


class TestPayloadCannotCarrySecrets:
    """구조적 방어 — 담을 필드가 없으면 샐 수 없다."""

    def test_renderer_accepts_only_the_payload(self):
        """렌더러가 원자료를 받을 수 있으면 언젠가 받게 된다."""
        import inspect

        for fn in (lead_report.render_lead_report_html, lead_report.render_lead_report_pdf):
            params = list(inspect.signature(fn).parameters)
            assert params == ["payload"], fn.__name__

    def test_payload_has_no_field_for_raw_responses_or_competitors(self):
        import dataclasses

        forbidden = {"raw_response", "raw_responses", "competitors", "competitor_mentions",
                     "source_urls", "actions", "recommendations", "excerpt"}
        names = {f.name for f in dataclasses.fields(lead_report.LeadReportPayload)}
        assert not (names & forbidden), names & forbidden

    def test_payload_built_from_secret_bearing_results_carries_none_of_them(self, payload):
        rendered = repr(payload)
        for secret in (COMPETITOR_NAME, RAW_EXCERPT, SOURCE_URL):
            assert secret not in rendered


class TestRenderedOutputIsClean:
    """산출물 방어 — HTML은 PDF 텍스트 레이어가 된다. 여기 없으면 거기에도 없다."""

    def test_html_contains_none_of_the_hidden_fields(self, payload):
        html = lead_report.render_lead_report_html(payload)
        for secret in (COMPETITOR_NAME, RAW_EXCERPT, SOURCE_URL):
            assert secret not in html, secret

    def test_html_still_shows_what_we_promised_to_disclose(self, payload):
        """가리는 데 성공했다고 공개할 것까지 사라지면 안 된다 (F5-1 · §2-2).

        방법론 공개가 이 제품의 차별점이므로, 질의 원문·프롬프트·모델명이 빠지면
        가림이 아니라 제품이 망가진 것이다.
        """
        html = lead_report.render_lead_report_html(payload)
        assert "장편한외과의원" in html
        assert "수서역 근처 외과 병원 추천해줘" in html          # 질의 원문
        assert "치질이 있는데 수서역 근처 병원 어디로 가야해?" in html
        assert lead_report.sov_engine.SYSTEM_PROMPT_SOV in html   # 시스템 프롬프트 전문
        assert "gpt-5.6-luna" in html                             # 답변 모델
        assert "gpt-4o-mini-2024-07-18" in html                   # 판정 모델
        assert "광고물이 아닙니다" in html                        # F5-5 고지
        assert "인공지능" in html                                 # F5-5 AI 생성 고지

    def test_platform_columns_use_api_and_model_names_not_product_names(self, payload):
        """'ChatGPT 9번 중 0번'이라고 쓰면 철회한 주장을 라벨로 되살리는 셈이다 (F5-1)."""
        html = lead_report.render_lead_report_html(payload)
        assert "OpenAI API · gpt-5.6-luna" in html
        assert "Google Gemini API · gemini-3.6-flash" in html

    def test_sample_limitation_is_disclosed(self, payload):
        """무료 진단은 기술통계다 — 표본 한계 고지가 숫자와 함께 있어야 한다 (F3-6)."""
        html = lead_report.render_lead_report_html(payload)
        assert "다른 질문에서는 다르게 나올 수 있습니다" in html

    @pytest.mark.skipif(
        os.getenv("REQUIRE_PDF_RENDER") is None,
        reason="weasyprint 네이티브 의존성이 필요하다. CI에서 REQUIRE_PDF_RENDER=1로 강제한다.",
    )
    def test_pdf_text_layer_contains_none_of_the_hidden_fields(self, payload):
        """최종 방어선 — 실제 PDF에서 텍스트를 추출해 확인한다.

        HTML 검사가 통과해도 렌더러가 어딘가에서 원자료를 끼워 넣을 수 있으므로,
        CI에서는 산출물 자체를 본다.
        """
        from io import BytesIO

        from pypdf import PdfReader

        pdf_bytes = lead_report.render_lead_report_pdf(payload)
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "".join(page.extract_text() or "" for page in reader.pages)

        assert text.strip(), "PDF에서 텍스트를 추출하지 못했다 — 검사가 무의미해진다"
        for secret in (COMPETITOR_NAME, RAW_EXCERPT, SOURCE_URL):
            assert secret not in text, secret


class TestDescriptiveStatistics:
    def test_query_disclosure_aggregates_safe_outcomes_without_raw_answers(self, payload):
        query = payload.queries[0]

        assert query.planned == 6
        assert query.measured == 4
        assert query.mentioned == 4
        assert query.failed == 2

    def test_report_contact_comes_from_typed_settings(self, monkeypatch):
        diagnosis = _diagnosis()
        monkeypatch.setattr(lead_report.settings, "LEAD_REPORT_CONTACT_NAME", "담당자")
        monkeypatch.setattr(lead_report.settings, "LEAD_REPORT_CONTACT_ROLE", "마케팅")
        monkeypatch.setattr(lead_report.settings, "LEAD_REPORT_CONTACT_EMAIL", "owner@example.com")
        monkeypatch.setattr(lead_report.settings, "LEAD_REPORT_CONTACT_PHONE", "070-0000-0000")

        payload = lead_report.build_lead_report_payload(
            diagnosis,
            _results(diagnosis),
            generated_at=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
        )

        assert payload.contact.name == "담당자"
        assert payload.contact.role == "마케팅"
        assert payload.contact.email == "owner@example.com"
        assert payload.contact.phone == "070-0000-0000"

    def test_failed_measurements_are_excluded_from_the_denominator_but_shown(self, payload):
        """실패를 분모에 넣으면 도구 장애가 병원 성과처럼 보인다.
        감추면 '9번 중 0번'과 '1번 중 0번'이 같아 보인다 — 빼되 보여준다(F3-5)."""
        segment = payload.segments[0]
        assert segment.planned == 9
        assert segment.failed == 1
        assert segment.measured == 8
        assert segment.mention_rate == round(segment.mentioned / 8 * 100, 1)

    def test_zero_successful_measurements_is_not_reported_as_zero_percent(self):
        """'측정 안 됨'과 '실제 0% 언급'은 다른 사실이다."""
        segment = lead_report.PlatformSegment(
            platform="chatgpt", vendor_label="OpenAI API", model="m",
            planned=9, measured=0, mentioned=0, failed=9,
        )
        assert segment.mention_rate is None

    def test_no_confidence_interval_language_appears(self, payload):
        """무료 진단은 기술통계로 확정됐다 — 모집단 추정 표현을 쓰지 않는다 (PRD §2-2)."""
        html = lead_report.render_lead_report_html(payload)
        for banned in ("신뢰구간", "95%", "추정치", "모집단"):
            assert banned not in html, banned

    def test_measurement_time_comes_from_the_data_not_from_now(self, payload):
        """캐시 적중분은 원본 측정 시각을 쓴다 — 생성 시각으로 덮으면 7일 전 답변이
        오늘 측정한 것처럼 보인다 (설계 T-15)."""
        assert payload.generated_at.date() == datetime(2026, 7, 30).date()
        for query in payload.queries:
            assert query.measured_at is not None
            assert query.measured_at.date() == datetime(2026, 7, 25).date()


class TestReadableDocument:
    """리포트는 원장님이 그대로 읽는 문서다 — 렌더 결과가 사고처럼 보이면 안 된다."""

    def test_no_guillemets_render_as_encoding_artifacts(self, payload):
        """지역을 «»로 감싸던 장식이 PDF 폰트에서 ≪ ≫로 나와 인코딩 오류처럼 보였다.

        실제로 원장에게 나간 첫 리포트가 `지역 ≪수서역≫`이었다. 장식용 기호는 쓰지 않는다.
        """
        html = lead_report.render_lead_report_html(payload)
        for artifact in ("«", "»", "≪", "≫"):
            assert artifact not in html, f"{artifact!r}가 리포트에 있습니다."

    def test_the_region_is_still_shown(self, payload):
        # 기호만 지우고 값까지 지우면 안 된다.
        assert payload.region in lead_report.render_lead_report_html(payload)

    @pytest.mark.skipif(
        os.getenv("REQUIRE_PDF_RENDER") is None,
        reason="weasyprint 네이티브 의존성이 필요하다. CI에서 REQUIRE_PDF_RENDER=1로 강제한다.",
    )
    def test_pdf_has_two_intentional_pages(self, payload):
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(lead_report.render_lead_report_pdf(payload)))

        assert len(reader.pages) == 2

    def test_zero_mentions_are_explained_not_left_bare(self):
        """0회는 가장 흔한 결과이고 가장 오해받기 쉽다.

        아무 설명 없이 "0번"만 두면 원장님은 병원 평가로 읽는다. 실제 의미는 AI가 인용할
        근거가 공개된 곳에 없다는 것이고, 그건 진료의 질과 다른 문제다.
        """
        diagnosis = _diagnosis()
        payload = lead_report.build_lead_report_payload(
            diagnosis,
            _results(diagnosis, mentioned_per_platform=0),
            generated_at=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
        )
        assert payload.total_mentioned == 0
        html = lead_report.render_lead_report_html(payload)
        assert "진료의 질에 대한 평가가 아닙니다" in html

    def test_the_zero_explanation_promises_no_improvement(self):
        """해석은 하되 개선을 약속하지 않는다 — 무료 진단이 파는 것은 측정이다.

        문서 전체에서 "보장"을 찾으면 안 된다. `언급은 보장되고 측정은 무의미해집니다`는
        질문에 병원명을 넣지 않는 이유를 설명하는 정직한 문장이라 걸리면 곤란하다.
        그래서 **0회 설명 문단만** 떼어 검사한다.
        """
        import re

        diagnosis = _diagnosis()
        payload = lead_report.build_lead_report_payload(
            diagnosis,
            _results(diagnosis, mentioned_per_platform=0),
            generated_at=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
        )
        html = lead_report.render_lead_report_html(payload)
        block = re.search(
            r'<div class="caveat">\s*(0번은[^<]*)</div>', html, re.S
        )
        assert block, "0회 설명 문단을 찾지 못했습니다."
        for promise in ("올려", "높여", "보장", "반드시", "개선해"):
            assert promise not in block.group(1), f'0회 설명에 약속 표현 "{promise}"이 있습니다.'

    def test_a_nonzero_result_does_not_get_the_zero_explanation(self, payload):
        # 언급이 있는데 "0번은 평가가 아닙니다"가 붙으면 문서가 자기 숫자를 부정한다.
        assert payload.total_mentioned > 0
        assert "진료의 질에 대한 평가가 아닙니다" not in lead_report.render_lead_report_html(payload)
