"""작가: 승인된 필수 문구(must_use_messages)는 의역 없이 원문 그대로 본문에 들어가야 저장된다.

9/26 장앤김 인시던트의 본문은 필수 문구를 의역했다(쉼표 누락, '조기 발견이' → '조기 발견과
제거가', 앞머리에 '국가암정보센터에 따르면'). 프롬프트가 "자연스럽게 반영"을 요구했기 때문이다.
이제 프롬프트는 원문 그대로를 요구하고, 생성 후 코드가 원문 포함을 확인한다. 빠지면 기존
재작성 루프·호출 예산 안에서만 다시 쓰고, 끝내 빠지면 GENERATION_REJECTED(본문 표본 실패)다.

이 파일은 기존 공개 경로(`generate_content`, 실행 분류기)만 쓴다. 모든 공급자 호출은 가짜이며
외부 네트워크는 막는다.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.models.content import ContentType
from app.services import content_engine
from app.workers.generation_retry_policy import GenerationRetryClass, retry_class_for
from app.workers.generation_run_control import classify_generation_failure
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)

# 장앤김 승인 must_use_messages 1번(원문)과 인시던트 본문의 의역 문장(uploads/f7ef_candidate.json).
MUST_USE_2CM = (
    "선종은 암이 나타나기 이전의 병변이며, 2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 "
    "조기 발견이 중요합니다."
)
MUST_USE_SCREENING = "국립암센터는 50대 이상에게 5~10년 주기의 대장내시경 검사를 권고하고 있습니다."
INCIDENT_PARAPHRASE = (
    "용종은 대부분 선종성 용종으로, 국가암정보센터에 따르면 선종은 암이 나타나기 이전의 병변이며 "
    "2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 조기 발견과 제거가 중요합니다."
)


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


def _hospital() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        address="서울시 노원구",
        phone="02-000-0000",
        business_hours=None,
        region=["노원"],
        specialties=["내과"],
        keywords=["대장내시경"],
        director_name="김의사",
        director_career="",
        director_philosophy="",
        treatments=[],
    )


def _philosophy(must_use: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        status="APPROVED",
        version=2,
        positioning_statement="",
        doctor_voice="",
        patient_promise="",
        content_principles=[],
        tone_guidelines=[],
        must_use_messages=list(must_use),
        avoid_messages=[],
        medical_ad_risk_rules=[],
        treatment_narratives=[],
    )


def _payload(*sentences: str) -> dict:
    body = (
        "## 준비\n테스트의원 김의사 원장이 노원에서 안내합니다. "
        + ("검사 전 준비 사항을 단계별로 설명합니다. " * 90)
        + "\n\n## 용종\n"
        + " ".join(sentences)
        + "\n\n## 주의\n"
        + ("검사 당일 주의할 점을 정리했습니다. " * 60)
    )
    return {
        "title": "대장내시경 검사 전 준비 안내",
        "body": body,
        "meta_description": (
            "대장내시경 검사 전 준비 과정과 주의사항을 단계별로 안내합니다. 식이 조절 방법을 확인하세요."
        ),
        "references": [],
        "faq_question": None,
        "faq_answer_summary": None,
    }


class _Writer:
    """가짜 작가 공급자. 호출 수와 회차별 메시지를 보관한다."""

    def __init__(self, payloads: list[dict]):
        self.payloads = payloads
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[min(len(self.calls), len(self.payloads)) - 1]
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))],
            stop_reason=None,
            usage=None,
            id="msg_test",
        )


def _install_writer(monkeypatch, writer: _Writer) -> None:
    async def record_attempt(**_kwargs):
        return None

    async def no_cost_record(*_args, **_kwargs):
        return None

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(content_engine.client.chat.completions, "create", writer.create)
    monkeypatch.setattr("app.services.provider_usage.record_attempt", record_attempt)
    monkeypatch.setattr("app.services.cost_guard.record_provider_call", no_cost_record)
    monkeypatch.setattr(content_engine.generate_content.retry, "sleep", no_sleep)


async def _generate(must_use: list[str]):
    return await content_engine.generate_content(
        _hospital(),
        ContentType.NOTICE,
        philosophy=_philosophy(must_use),
        content_brief={"must_use_messages": list(must_use)},
    )


def test_writer_prompt_requires_must_use_messages_verbatim_not_paraphrased() -> None:
    prompt = content_engine.STATIC_SYSTEM_BLOCK
    assert "자연스럽게 반영" not in prompt
    assert "must_use_messages" in prompt
    assert "의역하지 말고 원문 그대로" in prompt
    assert "독립된 문장" in prompt


async def test_verbatim_must_use_messages_are_accepted_in_one_call(monkeypatch) -> None:
    writer = _Writer([_payload(MUST_USE_2CM, MUST_USE_SCREENING)])
    _install_writer(monkeypatch, writer)

    saved = await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert MUST_USE_2CM in saved["body"]
    assert len(writer.calls) == 1


async def test_paraphrased_must_use_is_fed_back_and_rewritten_within_the_existing_loop(
    monkeypatch,
) -> None:
    """인시던트의 의역 문장은 저장되지 않고, 빠진 필수 문구가 다음 회차 지적으로 넘어간다."""

    writer = _Writer(
        [
            _payload(INCIDENT_PARAPHRASE, MUST_USE_SCREENING),
            _payload(MUST_USE_2CM, MUST_USE_SCREENING),
        ]
    )
    _install_writer(monkeypatch, writer)

    saved = await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert MUST_USE_2CM in saved["body"]
    assert INCIDENT_PARAPHRASE not in saved["body"]
    assert len(writer.calls) == 2
    second_user = writer.calls[1]["messages"][1]["content"]
    assert "직전 응답이 시스템 검증에서 거부" in second_user
    assert "must_use messages missing verbatim" in second_user
    assert "선종은 암이 나타나기 이전의" in second_user
    # 이미 들어 있던 필수 문구는 빠진 문구로 지목하지 않는다.
    assert "국립암센터는" not in second_user


async def test_must_use_still_missing_after_the_existing_budget_is_a_sample_rejection(
    monkeypatch,
) -> None:
    """새 루프·새 한도 없이 기존 재작성 3회에서 멈추고, 본문 표본 실패로 분류된다."""

    writer = _Writer([_payload(INCIDENT_PARAPHRASE, MUST_USE_SCREENING)])
    _install_writer(monkeypatch, writer)

    with pytest.raises(ValueError, match="must_use messages missing verbatim") as caught:
        await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert len(writer.calls) == content_engine.GENERATION_REMEDIATION_ROUNDS == 3
    assert content_engine.GENERATION_PROVIDER_CALL_BUDGET == 5
    code, message = classify_generation_failure(caught.value)
    assert code == "GENERATION_REJECTED"
    assert "필수 문구" in message
    assert retry_class_for(code) == GenerationRetryClass.SAMPLE_RECOVERABLE


@pytest.mark.parametrize(
    "written",
    [
        pytest.param(f"**{MUST_USE_2CM}**", id="markdown-bold"),
        pytest.param(MUST_USE_2CM.replace("2cm", "２ｃｍ"), id="full-width"),
        pytest.param(MUST_USE_2CM.replace(" ", "  "), id="spacing"),
        pytest.param(f"“{MUST_USE_2CM}”", id="quoted"),
        pytest.param(MUST_USE_2CM.rstrip("."), id="no-period-at-paragraph-end"),
    ],
)
async def test_normalized_verbatim_variants_are_accepted(monkeypatch, written: str) -> None:
    # 필수 문구를 문단 끝에 둔다 — 마침표가 없으면 문단 끝이어야 독립된 문장이다.
    writer = _Writer([_payload(MUST_USE_SCREENING, written)])
    _install_writer(monkeypatch, writer)

    await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert len(writer.calls) == 1


@pytest.mark.parametrize(
    "written",
    [
        pytest.param(MUST_USE_2CM.replace("병변이며,", "병변이며"), id="comma-dropped"),
        pytest.param(MUST_USE_2CM.replace("조기 발견이", "조기 발견과 제거가"), id="words-changed"),
        pytest.param(f"국가암정보센터에 따르면 {MUST_USE_2CM}", id="prefix-added"),
    ],
)
async def test_paraphrase_variants_are_rejected(monkeypatch, written: str) -> None:
    writer = _Writer([_payload(written, MUST_USE_SCREENING)])
    _install_writer(monkeypatch, writer)

    with pytest.raises(ValueError, match="must_use messages missing verbatim"):
        await _generate([MUST_USE_2CM, MUST_USE_SCREENING])


async def test_without_must_use_messages_generation_is_unchanged(monkeypatch) -> None:
    writer = _Writer([_payload(INCIDENT_PARAPHRASE)])
    _install_writer(monkeypatch, writer)

    await _generate([])

    assert len(writer.calls) == 1


def test_must_use_rejection_has_its_own_operator_message() -> None:
    code, message = classify_generation_failure(
        ValueError("Required must_use messages missing verbatim (1): ...")
    )
    assert code == "GENERATION_REJECTED"
    assert "필수 문구" in message
    assert "가격" not in message
