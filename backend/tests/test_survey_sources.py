"""원장 인터뷰 설문(P4-9, D32) — 개인정보 가림(E6)과 Google 공유 링크 정규화."""

import pytest

from app.models.essence import SourceType
from app.services import survey_sources as survey


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("원장 연락처 010-1234-5678", "010-1234-5678"),
        ("원장 연락처 01012345678", "01012345678"),
        ("원장 연락처 010 1234 5678", "010 1234 5678"),
        ("원장 연락처 +82 10-1234-5678", "10-1234-5678"),
        ("이메일 doctor.kim@clinic.co.kr 로 주세요", "doctor.kim@clinic.co.kr"),
        ("주민번호 800101-1234567", "800101-1234567"),
    ],
)
def test_personal_identifiers_are_masked(raw, secret) -> None:
    masked = survey.mask_personal_info(raw)
    assert "[가림]" in masked and secret not in masked


@pytest.mark.parametrize(
    "kept",
    [
        "병원 대표번호 02-123-4567",
        "진료시간 월 09:00-18:00 (점심 13:00-14:00)",
        "2026-10-14 개원 10주년",
        "치핵 수술 1,200건",
    ],
)
def test_clinic_facts_and_hours_are_not_masked(kept) -> None:
    assert survey.mask_personal_info(kept) == kept


def test_only_interview_text_is_masked() -> None:
    text = "원장 010-1234-5678"
    assert survey.mask_interview_text(SourceType.INTERVIEW, text) == "원장 [가림]"
    assert survey.mask_interview_text("INTERVIEW", text) == "원장 [가림]"
    assert survey.mask_interview_text(SourceType.HOMEPAGE, text) == text
    assert survey.mask_interview_text(SourceType.INTERVIEW, None) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://docs.google.com/document/d/1AbC_d-9/edit?usp=sharing",
            "https://docs.google.com/document/d/1AbC_d-9/export?format=txt",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1Sheet/edit#gid=12345",
            "https://docs.google.com/spreadsheets/d/1Sheet/export?format=csv&gid=12345",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1Sheet/edit?gid=7",
            "https://docs.google.com/spreadsheets/d/1Sheet/export?format=csv&gid=7",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1Sheet/",
            "https://docs.google.com/spreadsheets/d/1Sheet/export?format=csv",
        ),
        ("https://www.notion.so/clinic/survey", "https://www.notion.so/clinic/survey"),
    ],
)
def test_google_share_links_become_export_links(url, expected) -> None:
    assert survey.normalize_survey_url(url) == expected


@pytest.mark.parametrize(
    "url",
    ["https://docs.google.com/forms/d/e/1FAIp/viewform", "https://forms.gle/abc123"],
)
def test_google_forms_links_are_refused(url) -> None:
    with pytest.raises(survey.SurveyLinkError) as exc:
        survey.normalize_survey_url(url)
    assert "PDF" in str(exc.value)


def _patch_fetch(monkeypatch, responses):
    from app.services import asset_extractor as ax

    def target_for(url):
        return ax.FetchTarget(
            url=url,
            hostname="docs.google.com",
            port=443,
            allowed_ips=frozenset({"142.250.0.1"}),
        ), None

    monkeypatch.setattr(ax, "_validate_fetch_target", target_for)
    monkeypatch.setattr(ax, "_validate_response_peer", lambda _resp, _tgt: None)
    queue = list(responses)

    class _Resp:
        def __init__(self, status, headers, body):
            self.status_code = status
            self.headers = headers
            self.encoding = "utf-8"
            self.content = body.encode("utf-8")

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield self.content

    class _Stream:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, *args):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _Stream(_Resp(*queue.pop(0)))

    monkeypatch.setattr(ax.httpx, "AsyncClient", _Client)
    return ax


async def test_a_private_google_doc_redirecting_to_login_is_a_failure(monkeypatch) -> None:
    ax = _patch_fetch(
        monkeypatch,
        [(302, {"location": "https://accounts.google.com/ServiceLogin?continue=x"}, "")],
    )
    text, error, quality = await ax.fetch_url_text(
        "https://docs.google.com/document/d/1/export?format=txt"
    )
    assert text == "" and quality is None
    assert error == ax.GOOGLE_LOGIN_REQUIRED_MESSAGE


async def test_exported_plain_text_keeps_its_lines(monkeypatch) -> None:
    body = "Q1. 진료 철학은?\nA1. 환자의 이야기를 끝까지 듣습니다.\nQ2. 강조하고 싶은 점은?\nA2. 수술 전 충분한 설명"
    ax = _patch_fetch(monkeypatch, [(200, {"content-type": "text/plain; charset=utf-8"}, body)])
    text, error, _quality = await ax.fetch_url_text(
        "https://docs.google.com/document/d/1/export?format=txt"
    )
    assert error is None
    assert text.splitlines()[:2] == ["Q1. 진료 철학은?", "A1. 환자의 이야기를 끝까지 듣습니다."]


def test_spacing_dash_and_fullwidth_variants_are_masked() -> None:
    from app.services.survey_sources import mask_personal_info

    for raw in (
        "010  1234  5678",
        "010–1234–5678",
        "010—1234—5678",
        "800101–1234567",
        "800101 - 1234567",
        "０１０－１２３４－５６７８",
    ):
        assert mask_personal_info(f"연락 {raw} 끝") == "연락 [가림] 끝", raw
    kept = "대표 02-123-4567, 진료 09:00-18:00, 2024-10-14"
    assert mask_personal_info(kept) == kept


@pytest.mark.parametrize(
    "raw",
    [
        "０１０．１２３４．５６７８",
        "（０１０）１２３４－５６７８",
        "０１０　１２３４　５６７８",
        "(010)1234-5678",
        "010.1234.5678",
        "010 - 1234 - 5678",
        "+82 10-1234-5678",
        "+82 (0)10 1234 5678",
        "+82-10-1234-5678",
        "800101-1 234567",
        "80 01 01-1 234 567",
        "800101.1234567",
        "８００１０１－１２３４５６７",
    ],
)
def test_every_spacing_dot_paren_and_fullwidth_form_is_masked_whole(raw) -> None:
    from app.services.survey_sources import mask_personal_info

    assert mask_personal_info(f"연락 {raw} 끝") == "연락 [가림] 끝"
