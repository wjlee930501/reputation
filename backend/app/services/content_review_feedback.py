"""Deterministic content feedback: no provider calls, retries, or persistence.

Reference advice and duplicate topics remain non-blocking. Publication gates
are owned elsewhere; these helpers cannot grant publication permission.
"""

import logging
from typing import Any

from app.models.content import ContentItem

logger = logging.getLogger(__name__)


def review_findings(summary: object) -> list[str]:
    if not isinstance(summary, dict):
        return []
    findings = summary.get("findings")
    if not isinstance(findings, list):
        return []
    return [str(finding) for finding in findings if str(finding).strip()][:5]


_HARD_REMOVAL_KINDS = frozenset({"HOSPITAL_FACT", "MEDICAL_SAFETY"})

# 지적되지 않은 문단의 **사실관계**는 건드리지 않는다. 다만 "그대로 두라"고 말하면
# 삭제한 만큼 본문이 짧아진 채로 회차가 끝나 분량 하한에 걸린다. 지킬 것은 승인된
# 사실이지 문단의 길이가 아니므로, 이미 승인된 내용을 더 풀어 쓰는 길은 열어 둔다.
_HARD_REMOVAL_INSTRUCTION = (
    "아래 지적된 주장을 본문에서 삭제하거나 '개인차가 있습니다'·'정확한 내용은 의료기관에서 "
    "확인이 필요합니다'처럼 완화해 다시 쓰세요. 새로운 사실·수치·효과·장비·경력을 "
    "추가하지 말고, 지적되지 않은 문단의 사실관계도 바꾸지 마세요. 삭제로 줄어든 분량은 "
    "그 문단들이 이미 담고 있는 내용을 더 자세히 풀어 써서 채우세요."
)


def hard_removal_findings(review: Any) -> list[str]:
    """Turn model-declared fact/safety HARD findings into one removal instruction."""

    def _label(value: object) -> str:
        return str(getattr(value, "value", value) or "").upper()

    messages = [
        str(getattr(finding, "message", "")).strip()
        for finding in getattr(review, "blocking_findings", ())
        if _label(getattr(finding, "severity", None)) == "HARD"
        and _label(getattr(finding, "kind", None)) in _HARD_REMOVAL_KINDS
    ]
    messages = [message for message in messages if message]
    if not messages:
        return []
    return [_HARD_REMOVAL_INSTRUCTION, *messages]


_REFERENCE_FINDING_KIND = "REFERENCE"


def finding_label(value: object) -> str:
    return str(getattr(value, "value", value) or "").upper()


def review_finding_items(review: Any) -> list[Any]:
    """Findings as objects. 문자열만 넘어오는 구형 shape은 여기서 제외한다."""
    return [
        finding
        for finding in (getattr(review, "findings", ()) or ())
        if getattr(finding, "message", None) is not None
    ]


def apply_reference_review_findings(candidate: dict, review: Any) -> list[str]:
    """SOFT+REFERENCE 지적을 '지목된 참고자료 제거'로 결정적으로 처리한다.

    제목을 message에서 찾을 수 있으면 그 항목만 떼어 낸다(유료 재작성 없음).
    못 찾거나 마지막 한 건이라 떼면 근거가 비는 경우에는 글은 그대로 두고 조언만
    남긴다 — 이 지적은 어떤 경우에도 발행을 막지 않는다.
    """
    messages = [
        str(getattr(finding, "message", "")).strip()
        for finding in review_finding_items(review)
        if finding_label(getattr(finding, "kind", None)) == _REFERENCE_FINDING_KIND
        and finding_label(getattr(finding, "severity", None)) == "SOFT"
    ]
    messages = [message for message in messages if message]
    if not messages:
        return []
    references = candidate.get("references")
    if not isinstance(references, list):
        return messages
    joined = " ".join(messages)
    kept = [
        reference
        for reference in references
        if not (
            isinstance(reference, dict)
            and len(str(reference.get("title") or "").strip()) >= 4
            and str(reference.get("title")).strip() in joined
        )
    ]
    # 근거를 전부 떼면 발행 게이트가 막힌다. 조언으로만 남기고 원본을 지킨다.
    if kept and len(kept) != len(references):
        logger.info(
            "Dropping reviewer-flagged references: kept=%d of %d",
            len(kept),
            len(references),
        )
        candidate["references"] = kept
    return messages


def non_reference_remediation_messages(review: Any) -> list[str]:
    """재작성을 요구할 수 있는 지적만 남긴다 (REFERENCE는 결정적으로 처리됨)."""
    if not review_finding_items(review):
        # 구형 문자열 shape: 종류를 알 수 없으므로 종전대로 전부 넘긴다.
        return [
            str(message).strip()
            for message in (getattr(review, "remediation_messages", ()) or ())
            if str(message).strip()
        ]
    return [
        str(getattr(finding, "message", "")).strip()
        for finding in review_finding_items(review)
        if finding_label(getattr(finding, "kind", None)) != _REFERENCE_FINDING_KIND
        and str(getattr(finding, "message", "")).strip()
    ]


_DUPLICATE_TOPIC_INSTRUCTION = (
    "최근 발행한 글과 제목·주제가 거의 같습니다. 측정 키워드는 그대로 유지하되 다른 "
    "질문·관점·독자 상황을 골라 제목과 구성을 바꿔 다시 쓰세요. 비슷한 기존 제목: "
)


def first_h2_line(body: object) -> str:
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            return stripped[3:].strip()
    return ""


def duplicate_topic_matches(
    content: dict,
    existing_titles: list[str] | None,
    approved_brief: dict | None,
) -> list[tuple[str, float]]:
    """최근 제목과 사실상 같은 주제인가. 결정적 계산이라 공급자를 부르지 않는다."""
    if not existing_titles:
        return []
    from app.services.content_similarity import find_similar_titles

    return find_similar_titles(
        content.get("title") or "",
        first_h2_line(content.get("body")),
        (approved_brief or {}).get("target_keyword") or "",
        existing_titles,
    )


def duplicate_topic_remediation(matches: list[tuple[str, float]]) -> list[str]:
    if not matches:
        return []
    titles = ", ".join(title for title, _score in matches[:3])
    return [f"{_DUPLICATE_TOPIC_INSTRUCTION}{titles}"]


def screening_probe(content_data: dict) -> ContentItem:
    return ContentItem(
        title=content_data["title"],
        body=content_data["body"],
        meta_description=content_data.get("meta_description"),
        faq_question=content_data.get("faq_question"),
        faq_answer_summary=content_data.get("faq_answer_summary"),
    )
