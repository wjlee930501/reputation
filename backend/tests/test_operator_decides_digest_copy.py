"""아침 요약의 진료비·병원 선택 글 참고자료 보류 줄도 Admin에 실제로 있는 조작만 말한다.

인시던트 조치(`REFERENCES_OPERATOR_DECIDES_ACTION`)는 `tests/test_reference_operator_copy.py`가
지킨다. 요약 한 줄(`blocker_copy(REFERENCES_OPERATOR_DECIDES_COPY_CODE)`)은 따로 쓴 짧은 문구라
같은 검사를 여기서 한다 — 없는 조작(항목 종료·재생성·발행일 이동 …)을 말하지 않고, 인용한
조작(“콘텐츠 수정”·“참고 자료 추가”)이 콘텐츠 화면 소스에 있으며, 인시던트 조치와 같은 두
길(문서 추가 · 질환·검사 안내 글로 고치기)을 말한다. 요약은 조치 문장을 자르지 않고 통째로
싣는다는 것도 확인한다.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from app.services.content_publish_notifications import build_generation_blocked_digest_intent
from app.services.notification_copy import REFERENCES_OPERATOR_DECIDES_COPY_CODE, blocker_copy
from app.workers.generation_incident_control import (
    PREPUBLISH_MORNING_BATCH,
    PUBLISH_MORNING_BATCH,
    REFERENCES_OPERATOR_DECIDES_ACTION,
)
from tests.test_reference_operator_copy import NONEXISTENT_ACTIONS

_REPO = Path(__file__).resolve().parents[2]
_CONTENT_PAGE = _REPO / "admin" / "app" / "hospitals" / "[id]" / "content" / "page.tsx"

_DIGEST_ACTION = blocker_copy(REFERENCES_OPERATOR_DECIDES_COPY_CODE).action


def test_the_digest_line_names_no_control_the_content_screen_lacks():
    for phrase in NONEXISTENT_ACTIONS:
        assert phrase not in _DIGEST_ACTION, f"요약 줄이 없는 조작 '{phrase}'을(를) 말한다"


def test_every_quoted_control_in_the_digest_line_exists_on_the_content_screen():
    quoted = re.findall(r"“([^”]+)”", _DIGEST_ACTION)
    assert quoted == ["콘텐츠 수정", "참고 자료 추가"]
    page = _CONTENT_PAGE.read_text(encoding="utf-8")
    for label in quoted:
        assert label in page, f"요약 줄이 콘텐츠 화면에 없는 “{label}”을(를) 말한다"


@pytest.mark.parametrize(
    "fragment",
    [
        "“콘텐츠 수정”",
        "“참고 자료 추가”",
        "공공·학술 기관 문서",
        "제목·본문을 질환·검사 안내 글로 고쳐 저장",
        "참고 자료 없이는 발행되지 않",
        "자동 복구는 이 글을 다시 쓰지 않습니다.",
    ],
)
def test_the_digest_line_says_what_the_incident_action_says(fragment):
    """요약 줄은 인시던트 조치의 줄임말이다 — 같은 조작·같은 두 길·같은 결과를 말한다."""

    assert fragment in _DIGEST_ACTION
    assert fragment in REFERENCES_OPERATOR_DECIDES_ACTION


def _outcome(content_id: str, code: str, *, copy_code: str | None = None) -> dict[str, object]:
    return {
        "hospital_id": "hospital-a",
        "hospital_name": "비용보류의원",
        "content_id": content_id,
        "scheduled_date": "2026-10-01",
        "title": "치질 수술 비용 — 보험 적용과 본인부담",
        "code": code,
        "cause": "원인",
        "attempt_fingerprint": f"ctx-{content_id}",
        "copy_code": copy_code,
    }


def _hospital_line(outcomes) -> list[str]:
    intent = build_generation_blocked_digest_intent(
        date(2026, 10, 1), PUBLISH_MORNING_BATCH, outcomes
    )
    sections = [
        block["text"]["text"]
        for block in intent.message.payload()["blocks"]
        if block.get("type") == "section"
    ]
    return sections[1].splitlines()


@pytest.mark.parametrize("batch", [PREPUBLISH_MORNING_BATCH, PUBLISH_MORNING_BATCH])
def test_the_digest_carries_the_whole_action_when_it_is_the_only_one(batch):
    intent = build_generation_blocked_digest_intent(
        date(2026, 10, 1),
        batch,
        [_outcome("c-1", "MISSING_REFERENCES", copy_code=REFERENCES_OPERATOR_DECIDES_COPY_CODE)],
    )
    text = "\n".join(
        block["text"]["text"]
        for block in intent.message.payload()["blocks"]
        if block.get("type") == "section"
    )
    # 조치는 문장으로 쪼개거나 자르지 않고 한 줄 그대로다 — 두 문장 모두 남는다.
    assert f"  {_DIGEST_ACTION}" in text.splitlines()
    assert _DIGEST_ACTION.count(". ") == 1 and _DIGEST_ACTION.endswith(".")


def test_beside_one_other_blocker_the_whole_action_leads_in_gate_order():
    """요약은 서로 다른 조치를 게이트 순서(예정일·순번)대로 한 줄에 잇는다. 이 조치가 먼저면 통째로 앞에 선다."""

    lines = _hospital_line(
        [
            _outcome("c-1", "MISSING_REFERENCES", copy_code=REFERENCES_OPERATOR_DECIDES_COPY_CODE),
            _outcome("c-2", "CONTENT_NOT_GENERATED"),
        ]
    )
    other = blocker_copy("CONTENT_NOT_GENERATED").action
    assert lines[-1] == f"  {_DIGEST_ACTION} {other}"


_EARLIER_BLOCKERS = ("CONTENT_NOT_GENERATED", "MISSING_APPROVED_ESSENCE", "CONTENT_IMAGE_NOT_READY")


@pytest.mark.parametrize("earlier", [1, 2, 3], ids=["one_before", "two_before", "three_before"])
def test_the_operator_action_leads_even_when_other_blockers_come_first(earlier):
    """게이트 순서로 다른 조치가 먼저 와도 사람이 정할 글의 조치가 맨 앞에 서서 상한에 잘리지 않는다.

    상한(현재 2, #179 뒤 3)은 하드코딩하지 않고 렌더된 줄에서 읽는다 — 줄은 반드시
    [운영자 조치, 나머지(처음 본 순서)…]의 앞부분이어야 한다.
    """

    codes = _EARLIER_BLOCKERS[:earlier]
    lines = _hospital_line(
        [
            *(_outcome(f"c-{index}", code) for index, code in enumerate(codes)),
            _outcome("c-op", "MISSING_REFERENCES", copy_code=REFERENCES_OPERATOR_DECIDES_COPY_CODE),
        ]
    )
    others = [blocker_copy(code).action for code in codes]
    assert len(set(others)) == len(others)  # 모두 서로 다른 조치다
    expected = [_DIGEST_ACTION, *others]
    shown = [
        count
        for count in range(1, len(expected) + 1)
        if lines[-1] == "  " + " ".join(expected[:count])
    ]
    assert len(shown) == 1, lines[-1]
    assert shown[0] >= min(2, len(expected))  # 상한이 1로 줄지 않았다
    assert lines[-1].startswith(f"  {_DIGEST_ACTION}")
    # 제목·편수 줄은 그대로다.
    assert f"발행 보류 {earlier + 1}편" in lines[0]
    assert "참고 자료 운영자 판단 1편" in lines[1]
