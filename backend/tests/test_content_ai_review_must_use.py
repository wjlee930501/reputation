"""독립 AI 검수: 승인된 필수 문구(must_use_messages)를 원문 그대로 쓴 문장에 대한 지적.

9/26 장앤김 인시던트(f7ef9ffd)에서 검수자는 must_use_messages와 같은 문장을 인용해
"동일하지만 완화 필요"라는 HARD 지적을 냈고, 그 지적이 게시를 막았다. 필수 문구는
작가가 바꿀 수 없는 승인 자료이므로, 지적이 인용한 문장이 필수 문구 원문과 정규화 기준으로
완전히 같으면 그 지적은 기록만 남고 게시를 막지 않아야 한다. 무관한 지적, 일부만 겹치는
인용, 필수 문구에 말을 덧붙인 문장은 지금처럼 막혀야 한다.

이 파일은 기존 공개 경로(`review_generated_content`, 저장 payload, 발행 게이트)만 쓴다 —
새 심볼 없이 동작 차이로 판정한다. 모든 공급자 호출은 가짜이며 외부 네트워크는 막는다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import content_ai_review
from app.services.content_ai_review import ContentAiReviewStatus
from app.services.content_publication import public_candidate_review_safe
from app.services.content_review_feedback import (
    hard_removal_findings,
    non_reference_remediation_messages,
)
from app.workers.generation_retry_policy import has_model_declared_hard_finding
from tests.provider_network_guard_support import ExternalNetworkBlocked
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)

# ── 9/26 장앤김 인시던트 원문 ──────────────────────────────────────────────
# uploads/f7ef_real_finding.json — 게시를 막은 HARD 지적 원문(그대로).
INCIDENT_FINDING = {
    "kind": "MEDICAL_SAFETY",
    "message": (
        "본문에서 '선종은 암이 나타나기 이전의 병변이며 2cm 이상 크기의 선종은 암으로 진행될 수 "
        "있으므로 조기 발견과 제거가 중요합니다'라고 단정적으로 표현했습니다. 이는 approved_essence의 "
        "must_use_messages와 동일하지만, 의료광고 규제상 '진행될 수 있다'는 가능성 표현도 개별 환자 "
        "예후를 보장하는 것처럼 수용될 여지가 있습니다. '임상 근거가 있다'는 표현을 함께 명시하여 "
        "완화 필요."
    ),
    "severity": "HARD",
}
# 지적이 인용한 문장(2cm 문장) 그대로.
INCIDENT_CITED_SENTENCE = (
    "선종은 암이 나타나기 이전의 병변이며 2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 "
    "조기 발견과 제거가 중요합니다"
)
# 당시 승인된 must_use_messages(장앤김 승인 운영 기준 v2). 1번이 인시던트 문장의 원문이다 —
# 본문은 쉼표를 빼고 '조기 발견과 제거가'로 바꿔 썼다(의역).
APPROVED_MUST_USE = [
    "선종은 암이 나타나기 이전의 병변이며, 2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 조기 발견이 중요합니다.",
    "국립암센터는 50대 이상에게 5~10년 주기의 대장내시경 검사를 권고하고 있습니다.",
    "대장암은 조기 발견 시 생존율이 현저히 높아질 수 있다는 임상 근거가 있습니다.",
    "의료진의 경험도에 따라 선종 발견율에 유의미한 차이가 발생할 수 있으므로, 숙련된 전문의 진료와 보조 기술이 모두 중요합니다.",
    "조기에 발견된 소화기 질환은 치료 결과가 크게 달라질 수 있습니다.",
    "선종(암 전 단계 병변)을 조기에 제거하면 암으로의 진행을 예방할 수 있습니다.",
    "AI는 외부 요인(의료진 컨디션, 환경)에 영향받지 않아 일관되게 높은 검출력을 유지할 수 있습니다.",
]
# uploads/f7ef_candidate.json 본문 중 인시던트 문장이 든 문단(그대로).
INCIDENT_PARAGRAPH = (
    "대장내시경 검사 중 용종이 발견되면 같은 검사에서 이어서 제거하는 경우가 많습니다. "
    "용종은 대부분 선종성 용종으로, 국가암정보센터에 따르면 선종은 암이 나타나기 이전의 병변이며 "
    "2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 조기 발견과 제거가 중요합니다."
)
INCIDENT_TITLE = "경산 소화기내과 진료비, 왜 검사마다 다를까요?"
_PARAGRAPH_LEAD = "대장내시경 검사 중 용종이 발견되면 같은 검사에서 이어서 제거하는 경우가 많습니다."
OTHER_CLAIM = "본원은 AI 내시경으로 모든 용종을 빠짐없이 찾아냅니다."


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


def _candidate(paragraph: str, *extra_paragraphs: str) -> dict:
    body = "\n\n".join(
        [
            "## 용종제거술이 추가되면 비용이 달라집니다",
            paragraph,
            *extra_paragraphs,
            "## 내원 전 확인할 점",
            "구체적인 금액은 의료기관마다 다를 수 있으므로 내원 전 확인이 필요합니다.",
        ]
    )
    return {
        "title": INCIDENT_TITLE,
        "body": body,
        "meta_description": "소화기내과 진료비가 검사 목적과 건강보험 적용 여부에 따라 달라지는 이유를 설명합니다.",
        "faq_question": None,
        "faq_answer_summary": None,
        "references": [],
    }


def _philosophy(must_use: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        must_use_messages=list(must_use),
        avoid_messages=[],
        medical_ad_risk_rules=[],
        positioning_statement="",
        doctor_voice="",
        content_principles=[],
        treatment_narratives=[],
    )


def _finding(*, quote: str | None, **overrides) -> dict:
    finding = dict(INCIDENT_FINDING)
    if quote is not None:
        finding["quote"] = quote
    finding.update(overrides)
    return finding


def _verdict(*findings: dict) -> dict:
    return {
        "decision": "REVISE",
        "confidence": 0.9,
        "findings": list(findings),
        "summary": "의료 안전 지적",
    }


def _install_reviewer(monkeypatch, verdicts: list[dict]):
    """검수 공급자·비용 가드·usage 원장을 흉내낸다. 공급자 응답은 강제 도구 호출 형태."""

    calls: list[dict] = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            payload = verdicts[min(len(calls), len(verdicts)) - 1]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name=content_ai_review.REVIEW_TOOL_NAME,
                        input=payload,
                    )
                ],
                stop_reason="tool_use",
                id="msg_test",
                usage=None,
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    monkeypatch.setattr(content_ai_review, "_llm_client", lambda: client)
    monkeypatch.setattr(content_ai_review.settings, "OPENROUTER_API_KEY", "test-key")

    async def reserve(category, **_kwargs):
        return SimpleNamespace(allowed=True, receipt=SimpleNamespace(category=category))

    async def settle(_receipt, *, consumed_units, **_kwargs):
        return None

    async def record_provider_call(_category, **_kwargs):
        return None

    async def record_attempt(**_kwargs):
        return True

    monkeypatch.setattr(content_ai_review.cost_guard, "reserve", reserve)
    monkeypatch.setattr(content_ai_review.cost_guard, "settle_reservation", settle)
    monkeypatch.setattr(
        content_ai_review.cost_guard, "record_provider_call", record_provider_call
    )
    from app.services import provider_usage

    monkeypatch.setattr(provider_usage, "record_attempt", record_attempt)
    return calls


async def _review(monkeypatch, *, candidate: dict, must_use: list[str], verdict: dict):
    calls = _install_reviewer(monkeypatch, [verdict])
    review = await content_ai_review.review_generated_content(
        hospital=SimpleNamespace(id=None, name="장앤김더나은속내과의원"),
        philosophy=_philosophy(must_use),
        content=candidate,
        content_brief={"must_use_messages": list(must_use)},
    )
    # 가짜 공급자만 불렸다(에스컬레이션 없음).
    assert len(calls) == 1
    return review


def _stored_item(candidate: dict, review) -> SimpleNamespace:
    """발행·공개 게이트가 읽는 저장 행 모양."""

    return SimpleNamespace(
        title=candidate["title"],
        body=candidate["body"],
        meta_description=candidate["meta_description"],
        faq_question=candidate["faq_question"],
        faq_answer_summary=candidate["faq_answer_summary"],
        references_list=candidate["references"],
        essence_check_summary={"ai_review": review.payload()},
    )


def _assert_blocks(candidate: dict, review) -> None:
    payload = review.payload()
    assert review.status == ContentAiReviewStatus.REVISE
    assert payload["blocking"] is True
    assert public_candidate_review_safe(_stored_item(candidate, review)) is False


# ── 회귀: 필수 문구를 그대로 쓴 문장에 대한 인시던트 지적은 게시를 막지 않는다 ──────


@pytest.mark.parametrize(
    "must_use_first",
    [
        pytest.param(APPROVED_MUST_USE[0], id="approved-must-use-2cm"),
        pytest.param(INCIDENT_CITED_SENTENCE, id="incident-cited-2cm-sentence"),
    ],
)
async def test_incident_finding_on_verbatim_must_use_sentence_no_longer_blocks(
    monkeypatch, must_use_first: str
) -> None:
    """9/26 장앤김: 필수 문구를 원문 그대로 쓴 2cm 문장에 대한 HARD 지적(원문)이 게시를 막지 않는다."""

    must_use = [must_use_first, *APPROVED_MUST_USE[1:]]
    sentence = must_use_first if must_use_first.endswith(".") else f"{must_use_first}."
    candidate = _candidate(f"{_PARAGRAPH_LEAD} {sentence}")
    # 검수자는 지적한 문장을 후보에서 그대로 복사해 quote로 낸다.
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=must_use,
        verdict=_verdict(_finding(quote=sentence)),
    )

    assert review.status == ContentAiReviewStatus.PASS
    assert review.blocking_findings == ()
    payload = review.payload()
    assert payload["blocking"] is False
    # 기록은 남는다 — 원문 지적, 모델이 매긴 심각도, 문제 종류.
    [recorded] = payload["findings"]
    assert recorded["message"] == " ".join(INCIDENT_FINDING["message"].split())
    assert recorded["kind"] == "MEDICAL_SAFETY"
    assert recorded["severity"] == "SOFT"
    assert recorded["original_severity"] == "HARD"
    assert recorded["target"] == "MUST_USE_MESSAGE"
    assert recorded["quote"] == sentence
    # 발행·공개 게이트, 재시도 분류, 재작성 지시 어디에서도 차단 사유가 아니다.
    assert public_candidate_review_safe(_stored_item(candidate, review)) is True
    assert has_model_declared_hard_finding(payload) is False
    assert hard_removal_findings(review) == []
    assert non_reference_remediation_messages(review) == []


async def test_uncertain_finding_on_verbatim_must_use_sentence_is_recorded_not_blocking(
    monkeypatch,
) -> None:
    sentence = APPROVED_MUST_USE[0]
    candidate = _candidate(f"{_PARAGRAPH_LEAD} {sentence}")
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE,
        verdict=_verdict(_finding(quote=sentence, severity="UNCERTAIN")),
    )

    assert review.status == ContentAiReviewStatus.PASS
    [recorded] = review.payload()["findings"]
    assert recorded["original_severity"] == "UNCERTAIN"
    assert recorded["target"] == "MUST_USE_MESSAGE"


async def test_must_use_finding_is_recorded_but_not_sent_to_the_writer_when_another_hard_blocks(
    monkeypatch,
) -> None:
    """무관한 HARD는 계속 막고, 필수 문구 지적은 삭제형 재작성 지시에 들어가지 않는다."""

    sentence = APPROVED_MUST_USE[0]
    candidate = _candidate(f"{_PARAGRAPH_LEAD} {sentence}", OTHER_CLAIM)
    other = {
        "severity": "HARD",
        "kind": "HOSPITAL_FACT",
        "quote": OTHER_CLAIM,
        "message": f"'{OTHER_CLAIM}'는 승인 자료에서 확인할 수 없는 병원 고유 성과입니다.",
    }
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE,
        verdict=_verdict(_finding(quote=sentence), other),
    )

    _assert_blocks(candidate, review)
    assert [finding.message for finding in review.blocking_findings] == [other["message"]]
    targets = {finding["message"]: finding["target"] for finding in review.payload()["findings"]}
    assert targets[other["message"]] == "CANDIDATE_TEXT"
    assert targets[" ".join(INCIDENT_FINDING["message"].split())] == "MUST_USE_MESSAGE"
    removal = hard_removal_findings(review)
    assert other["message"] in removal
    assert all("must_use_messages와 동일" not in message for message in removal)
    assert non_reference_remediation_messages(review) == [other["message"]]


def test_reviewer_is_asked_to_quote_the_sentence_it_flags() -> None:
    finding_schema = content_ai_review.REVIEW_TOOL["input_schema"]["properties"]["findings"][
        "items"
    ]
    assert "quote" in finding_schema["properties"]
    assert "quote" in finding_schema["required"]
    assert "quote" in content_ai_review._SYSTEM_PROMPT


# ── 대조군: 아래는 지금처럼 게시를 막아야 한다 ────────────────────────────────


@pytest.mark.parametrize(
    "must_use_first",
    [
        pytest.param(APPROVED_MUST_USE[0], id="approved-must-use"),
        pytest.param(INCIDENT_CITED_SENTENCE, id="incident-cited-sentence"),
    ],
)
@pytest.mark.parametrize(
    "quote",
    [
        pytest.param(INCIDENT_CITED_SENTENCE, id="quote-as-cited"),
        pytest.param(INCIDENT_PARAGRAPH.split("많습니다. ")[1], id="quote-full-body-sentence"),
    ],
)
async def test_actual_incident_body_sentence_still_blocks(
    monkeypatch, must_use_first: str, quote: str
) -> None:
    """당시 본문은 필수 문구를 의역했고 앞에 '국가암정보센터에 따르면' 등을 덧붙였다.

    필수 문구 원문과 다른 문장이므로(쉼표·'과 제거'·앞머리) 일치가 아니다 — 보수적 기본값.
    이 경로의 해결은 작가 쪽 원문 보존 검증이다(test_content_engine_must_use_verbatim).
    """

    candidate = _candidate(INCIDENT_PARAGRAPH)
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=[must_use_first, *APPROVED_MUST_USE[1:]],
        verdict=_verdict(_finding(quote=quote)),
    )

    _assert_blocks(candidate, review)
    assert has_model_declared_hard_finding(review.payload()) is True


async def test_unrelated_hard_finding_still_blocks(monkeypatch) -> None:
    candidate = _candidate(f"{_PARAGRAPH_LEAD} {APPROVED_MUST_USE[0]}", OTHER_CLAIM)
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE,
        verdict=_verdict(
            {
                "severity": "HARD",
                "kind": "HOSPITAL_FACT",
                "quote": OTHER_CLAIM,
                "message": f"'{OTHER_CLAIM}'는 승인 자료에서 확인할 수 없습니다.",
            }
        ),
    )

    _assert_blocks(candidate, review)


async def test_hard_finding_without_a_quote_still_blocks(monkeypatch) -> None:
    """인용문이 없으면 무엇을 짚었는지 대조할 수 없다 — 메시지 안의 인용으로 추정하지 않는다."""

    candidate = _candidate(f"{_PARAGRAPH_LEAD} {APPROVED_MUST_USE[0]}")
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE,
        verdict=_verdict(_finding(quote=None)),
    )

    _assert_blocks(candidate, review)


@pytest.mark.parametrize(
    "quote",
    [
        pytest.param(
            "2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 조기 발견이 중요합니다.",
            id="second-half-only",
        ),
        pytest.param("선종은 암이 나타나기 이전의 병변이며,", id="first-clause-only"),
        pytest.param("진행될 수 있다", id="short-expression"),
    ],
)
async def test_partial_overlap_with_must_use_still_blocks(monkeypatch, quote: str) -> None:
    candidate = _candidate(f"{_PARAGRAPH_LEAD} {APPROVED_MUST_USE[0]}")
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE,
        verdict=_verdict(_finding(quote=quote)),
    )

    _assert_blocks(candidate, review)


_APPENDED_SENTENCE = (
    APPROVED_MUST_USE[0].rstrip(".")
    + "며, 제거하면 암을 확실히 막을 수 있으니 반드시 검사를 받으세요."
)


@pytest.mark.parametrize(
    "quote",
    [
        pytest.param(_APPENDED_SENTENCE, id="quote-covers-appended-sentence"),
        pytest.param(APPROVED_MUST_USE[0], id="quote-trims-the-appended-risk"),
    ],
)
async def test_must_use_sentence_with_appended_risky_text_still_blocks(
    monkeypatch, quote: str
) -> None:
    """본문이 필수 문구에 위험 표현을 이어 붙였으면, 인용이 필수 문구만 잘라 와도 일치가 아니다."""

    candidate = _candidate(f"{_PARAGRAPH_LEAD} {_APPENDED_SENTENCE}")
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE,
        verdict=_verdict(_finding(quote=quote)),
    )

    _assert_blocks(candidate, review)


async def test_finding_that_also_quotes_another_candidate_sentence_still_blocks(
    monkeypatch,
) -> None:
    candidate = _candidate(f"{_PARAGRAPH_LEAD} {APPROVED_MUST_USE[0]}", OTHER_CLAIM)
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE,
        verdict=_verdict(
            _finding(
                quote=APPROVED_MUST_USE[0],
                message=(
                    f"'{APPROVED_MUST_USE[0]}'와 '{OTHER_CLAIM}'가 함께 예후를 단정합니다."
                ),
            )
        ),
    )

    _assert_blocks(candidate, review)


async def test_verbatim_must_use_is_not_demoted_when_it_is_not_an_approved_message(
    monkeypatch,
) -> None:
    """승인 목록에 없는 문장은 필수 문구가 아니다."""

    sentence = APPROVED_MUST_USE[0]
    candidate = _candidate(f"{_PARAGRAPH_LEAD} {sentence}")
    review = await _review(
        monkeypatch,
        candidate=candidate,
        must_use=APPROVED_MUST_USE[1:],
        verdict=_verdict(_finding(quote=sentence)),
    )

    _assert_blocks(candidate, review)


# ── 외부 호출 0건 가드 자체의 검증 ───────────────────────────────────────────


def test_network_guard_blocks_provider_http_and_raw_sockets(forbid_external_network) -> None:
    with pytest.raises(ExternalNetworkBlocked):
        httpx.Client().post("https://openrouter.ai/api/v1/chat/completions", json={})
    import socket

    with pytest.raises(ExternalNetworkBlocked):
        socket.create_connection(("api.openai.com", 443))
    with pytest.raises(ExternalNetworkBlocked):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("api.anthropic.com", 443))
    assert [channel for channel, _target in forbid_external_network] == [
        "httpx.HTTPTransport",
        "socket.create_connection",
        "socket.connect",
    ]
    # 이 테스트는 가드가 막았음을 확인했으므로 teardown의 0건 단언을 위해 비운다.
    forbid_external_network.clear()


def test_incident_fixture_matches_the_uploaded_finding() -> None:
    """회귀 입력이 첨부 원문에서 벗어나지 않았는지 — 인용 문장과 문단이 서로 일치한다."""

    assert f"'{INCIDENT_CITED_SENTENCE}'" in INCIDENT_FINDING["message"]
    assert INCIDENT_CITED_SENTENCE in INCIDENT_PARAGRAPH
    assert json.loads(json.dumps(INCIDENT_FINDING, ensure_ascii=False)) == INCIDENT_FINDING
