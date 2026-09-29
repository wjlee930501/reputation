"""필수 문구 검수 테스트 공용: 독립 검수 공급자·비용 가드·usage 원장을 흉내낸다.

실제 `review_generated_content`의 판정 경로를 그대로 타되, 공급자 응답만 강제 도구 호출
형태의 고정 판정으로 바꾼다. 외부 호출은 없다(`provider_network_guard_support`가 확인).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services import content_ai_review

# 현재 승인본의 필수 문구(1cm)와, 승인이 바뀌기 전 가이드(brief)에 저장된 옛 문구(2cm).
APPROVED_1CM = (
    "선종은 암이 나타나기 이전의 병변이며, 1cm 이상 크기의 선종은 암으로 진행될 수 있으므로 "
    "조기 발견이 중요합니다."
)
STALE_BRIEF_2CM = (
    "선종은 암이 나타나기 이전의 병변이며, 2cm 이상 크기의 선종은 암으로 진행될 수 있으므로 "
    "조기 발견이 중요합니다."
)
LEAD = "대장 선종은 정기 검진으로 찾을 수 있는 병변입니다."


def hard_finding(quote: str, message: str) -> dict:
    return {"severity": "HARD", "kind": "MEDICAL_SAFETY", "quote": quote, "message": message}


def verdict(*findings: dict) -> dict:
    return {
        "decision": "REVISE",
        "confidence": 0.9,
        "findings": list(findings),
        "summary": "의료 안전 지적",
    }


def approved_and_stale_verdict() -> dict:
    """승인본 1cm 문장과 옛 가이드 2cm 문장을 각각 원문 그대로 인용한 HARD 두 건."""

    return verdict(
        hard_finding(
            APPROVED_1CM,
            "필수 문구와 같은 문장이지만 가능성 표현도 완화가 필요합니다.",
        ),
        hard_finding(
            STALE_BRIEF_2CM,
            "크기 기준이 현재 승인된 기준과 다릅니다. 철회된 수치를 게시하면 안 됩니다.",
        ),
    )


def install_fake_reviewer(monkeypatch, verdicts: list[dict]) -> list[dict]:
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


def findings_by_quote(payload: dict) -> dict[str, dict]:
    return {finding.get("quote"): finding for finding in payload["findings"]}
