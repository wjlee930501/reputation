"""아침 요약의 진료비·병원 선택 글 참고자료 보류 줄도 Admin에 실제로 있는 조작만 말한다.

인시던트 조치(`REFERENCES_OPERATOR_DECIDES_ACTION`)는 `tests/test_reference_operator_copy.py`가
지킨다. 요약 한 줄(`blocker_copy(REFERENCES_OPERATOR_DECIDES_COPY_CODE)`)은 따로 쓴 짧은 문구라
같은 검사를 여기서 한다 — 없는 조작(항목 종료·재생성·발행일 이동 …)을 말하지 않고, 인용한
조작(“콘텐츠 수정”·“참고 자료 추가”)이 콘텐츠 화면 소스에 있으며, 인시던트 조치와 같은 두
길(문서 추가 · 질환·검사 안내 글로 고치기)을 말한다. 요약은 조치 문장을 자르지 않고 통째로
싣는다는 것도 확인한다.

아직 쓰이지 않은 슬롯은 고칠 제목·본문이 없으므로 '새로 쓰기' 문구를 따로 쓴다. 두 문구 모두
진료비·병원 선택 글에 검증된 문서 목록의 문서를 넣으면 저장이 거절된다는 것(PATCH 422)과,
'다시 쓰지 않습니다'가 생성 조건(승인된 운영 기준 등)이 바뀌기 전까지라는 단서를 싣는다.

Admin 소스(`admin/`)가 없는 backend 전용 체크아웃에서는 건너뛰고 CI에서는 실패한다
(`tests/test_reference_operator_copy.require_admin_source`와 같은 규칙).
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path

import pytest

from app.services.content_publish_notifications import build_generation_blocked_digest_intent
from app.services.notification_copy import (
    REFERENCES_OPERATOR_DECIDES_COPY_CODE,
    REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE,
    blocker_copy,
)
from app.workers.generation_incident_control import (
    PREPUBLISH_MORNING_BATCH,
    PUBLISH_MORNING_BATCH,
    REFERENCES_OPERATOR_DECIDES_ACTION,
)
from tests.test_reference_operator_copy import NONEXISTENT_ACTIONS, require_admin_source

_REPO = Path(__file__).resolve().parents[2]
_CONTENT_PAGE = _REPO / "admin" / "app" / "hospitals" / "[id]" / "content" / "page.tsx"

_DIGEST_ACTION = blocker_copy(REFERENCES_OPERATOR_DECIDES_COPY_CODE).action
_UNWRITTEN_ACTION = blocker_copy(REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE).action
_BOTH_ACTIONS = {"written": _DIGEST_ACTION, "unwritten": _UNWRITTEN_ACTION}


def _require_admin(*, environ=None) -> None:
    require_admin_source((_CONTENT_PAGE,), environ=os.environ if environ is None else environ)


@pytest.fixture(autouse=True)
def _admin_source_present(request):
    if "admin_source_guard" not in request.node.name:
        _require_admin()


def _guard_outcome(environ) -> str:
    # skip·fail은 BaseException이다 — 그대로 새면 이 테스트 자체가 건너뛰기로 끝나 뮤턴트가 산다.
    try:
        _require_admin(environ=environ)
    except pytest.fail.Exception:
        return "fail"
    except pytest.skip.Exception:
        return "skip"
    return "ok"


def test_admin_source_guard_fails_under_ci_and_skips_locally(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.modules[__name__], "_CONTENT_PAGE", tmp_path / "page.tsx")

    assert _guard_outcome({"CI": "true"}) == "fail"
    assert _guard_outcome({}) == "skip"


def test_admin_source_guard_is_wired_to_every_test_here(request):
    # 이 모듈의 다른 테스트가 모두 가드 뒤에서 돈다 — 가드를 빼면 admin/ 없는 체크아웃에서 실패한다.
    assert "_admin_source_present" in request.fixturenames


@pytest.mark.parametrize("name", sorted(_BOTH_ACTIONS))
def test_the_digest_line_names_no_control_the_content_screen_lacks(name):
    for phrase in NONEXISTENT_ACTIONS:
        assert phrase not in _BOTH_ACTIONS[name], f"요약 줄이 없는 조작 '{phrase}'을(를) 말한다"


@pytest.mark.parametrize("name", sorted(_BOTH_ACTIONS))
def test_every_quoted_control_in_the_digest_line_exists_on_the_content_screen(name):
    quoted = re.findall(r"“([^”]+)”", _BOTH_ACTIONS[name])
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
        "자동 복구는 운영 기준이 새로 승인되는 등 생성 조건이 바뀌기 전에는 이 글을 다시 쓰지 않습니다.",
    ],
)
def test_the_digest_line_says_what_the_incident_action_says(fragment):
    """요약 줄은 인시던트 조치의 줄임말이다 — 같은 조작·같은 두 길·같은 결과를 말한다."""

    assert fragment in _DIGEST_ACTION
    assert fragment in REFERENCES_OPERATOR_DECIDES_ACTION


_CURATED_422 = "진료비·병원 선택 글에는 검증된 문서 목록의 문서를 넣을 수 없어 저장이 거절됩니다."
_CAVEAT = "운영 기준이 새로 승인되는 등 생성 조건이 바뀌기 전에는"


def test_the_written_digest_line_is_pinned_sentence_for_sentence():
    assert _DIGEST_ACTION == (
        "콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러, 글의 주장을 직접 뒷받침하는 공공·학술 기관 "
        "문서를 “참고 자료 추가”로 넣거나 제목·본문을 질환·검사 안내 글로 고쳐 저장해 주세요. "
        f"{_CURATED_422} "
        f"참고 자료 없이는 발행되지 않고, 자동 복구는 {_CAVEAT} 이 글을 다시 쓰지 않습니다."
    )


# 원고 없는 슬롯의 요약 줄(#187 2차 s3)은 인시던트 조치(C1)와 같은 두 문장으로 끝난다 — 질환·검사
# 안내 글로 쓰면 자동 복구가 참고 자료를 찾는다는 예외와, 그대로 두면 쓰지 않는다는 결과.
_UNWRITTEN_EXCEPTION = "질환·검사 안내 글로 쓰면 참고 자료 없이 저장해도 자동 복구가 참고 자료를 찾습니다."
_UNWRITTEN_OUTCOME = (
    "그대로 두면 생성 조건이 바뀌기 전에는 자동 복구가 이 글을 쓰지 않고, 참고 자료 없이는 "
    "발행되지 않습니다."
)


def test_the_unwritten_digest_line_ends_like_the_unwritten_incident_action():
    from tests.test_generation_incident_copy import C1_UNWRITTEN_OPERATOR_DECIDES

    assert _UNWRITTEN_ACTION.endswith(f"{_UNWRITTEN_EXCEPTION} {_UNWRITTEN_OUTCOME}")
    assert C1_UNWRITTEN_OPERATOR_DECIDES.endswith(f"{_UNWRITTEN_EXCEPTION} {_UNWRITTEN_OUTCOME}")
    assert "다시 쓰지 않습니다" not in _UNWRITTEN_ACTION


def test_the_unwritten_digest_line_asks_for_a_new_draft_not_an_edit():
    assert _UNWRITTEN_ACTION == (
        "아직 원고가 없는 글입니다. 콘텐츠 탭에서 이 글의 “콘텐츠 수정”을 눌러, 질환·검사 안내 "
        "글로 제목·본문을 새로 쓰거나 글의 주장을 직접 뒷받침하는 공공·학술 기관 문서를 “참고 자료 "
        f"추가”로 함께 넣어 새 원고를 저장해 주세요. {_CURATED_422} "
        f"{_UNWRITTEN_EXCEPTION} {_UNWRITTEN_OUTCOME}"
    )
    assert "고쳐" not in _UNWRITTEN_ACTION  # 고칠 원고가 없다
    assert _UNWRITTEN_ACTION != _DIGEST_ACTION
    assert (
        blocker_copy(REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE).title
        == blocker_copy(REFERENCES_OPERATOR_DECIDES_COPY_CODE).title
    )


@pytest.mark.parametrize("name", sorted(_BOTH_ACTIONS))
def test_both_digest_lines_warn_about_the_curated_422_and_bound_the_no_rewrite_promise(name):
    action = _BOTH_ACTIONS[name]
    assert _CURATED_422 in action
    # 원고 없는 슬롯은 인시던트 조치(C1)와 같은 말로 한정한다(#187 2차 s3).
    assert (_CAVEAT if name == "written" else "생성 조건이 바뀌기 전에는") in action


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
    # 조치는 문장으로 쪼개거나 자르지 않고 한 줄 그대로다 — 세 문장 모두 남는다.
    assert f"  {_DIGEST_ACTION}" in text.splitlines()
    assert _DIGEST_ACTION.count(". ") == 2 and _DIGEST_ACTION.endswith(".")


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
    """게이트 순서로 다른 조치가 먼저 와도 사람이 정할 글의 조치가 맨 앞에 서고, 나머지 조치도
    처음 본 순서대로 모두 실린다(조치 문장 개수 상한은 없다).
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
    assert lines[-1] == "  " + " ".join([_DIGEST_ACTION, *others])
    # 제목·편수 줄은 그대로다.
    assert f"발행 보류 {earlier + 1}편" in lines[0]
    assert "참고 자료 운영자 판단 1편" in lines[1]


def test_written_and_unwritten_holds_both_lead_and_keep_their_own_words():
    """한 병원에 두 모양이 함께 있으면 두 문구 모두 다른 조치보다 앞에 선다(처음 본 순서)."""

    lines = _hospital_line(
        [
            _outcome("c-1", "CONTENT_NOT_GENERATED"),
            _outcome(
                "c-2",
                "MISSING_REFERENCES",
                copy_code=REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE,
            ),
            _outcome("c-3", "MISSING_REFERENCES", copy_code=REFERENCES_OPERATOR_DECIDES_COPY_CODE),
        ]
    )
    other = blocker_copy("CONTENT_NOT_GENERATED").action
    assert lines[-1] == f"  {_UNWRITTEN_ACTION} {_DIGEST_ACTION} {other}"
    assert "참고 자료 운영자 판단 2편" in lines[1]
