"""진료비·병원 선택 글의 참고 자료 보류 문구는 Admin에 실제로 있는 조작만 말한다(리뷰 3차 F5).

예전 문구의 '해당 항목을 종료'는 막다른 길이었다 — 콘텐츠 화면에는 항목 종료 버튼이 없고
(`admin/lib/content-page-contract.test.ts`가 '/cancel'·'콘텐츠 항목 종료'를 금지한다), 운영 센터의
조작은 다시 시도·해결·담당 지정뿐이다. 문구가 이름 붙이는 조작(“콘텐츠 수정”·“참고 자료 추가”,
제목·본문 편집, 저장)이 콘텐츠 화면 소스에 실제로 있는지, 없는 조작을 말하지 않는지 확인한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.admin.content import CURATED_REFERENCE_NOT_ALLOWED_MESSAGE
from app.workers.generation_incident_control import REFERENCES_OPERATOR_DECIDES_ACTION

_REPO = Path(__file__).resolve().parents[2]
_CONTENT_PAGE = _REPO / "admin" / "app" / "hospitals" / "[id]" / "content" / "page.tsx"
_CONTENT_CONTRACT = _REPO / "admin" / "lib" / "content-page-contract.test.ts"

COPIES = {
    "operator_decides_action": REFERENCES_OPERATOR_DECIDES_ACTION,
    "patch_422_message": CURATED_REFERENCE_NOT_ALLOWED_MESSAGE,
}
# 콘텐츠 화면에 없는 조작(또는 불가능한 결과). 문구가 이것을 권하면 운영자는 막다른 길에 선다.
NONEXISTENT_ACTIONS = (
    "종료",
    "닫기",
    "닫으",
    "취소",
    "재생성",
    "다시 생성",
    "즉시 발행",
    "지금 발행",
    "발행일",
    "일정 변경",
    "/cancel",
    "병원 공식 자료",
    "없이 발행하",
)


def _page() -> str:
    return _CONTENT_PAGE.read_text(encoding="utf-8")


def test_the_content_screen_really_has_no_close_or_regenerate_action():
    """전제 — 콘텐츠 화면 계약이 항목 종료·재생성·발행일 이동을 금지한다."""

    contract = _CONTENT_CONTRACT.read_text(encoding="utf-8")
    for fragment in ("'/cancel'", "'콘텐츠 항목 종료'", "'즉시 재생성'", "'발행일 옮기기'"):
        assert fragment in contract
    page = _page()
    assert "/cancel" not in page and "콘텐츠 항목 종료" not in page


def test_the_edit_controls_the_copy_names_exist_on_the_content_screen():
    page = _page()
    # 막힌 글의 배너 버튼 — DRAFT·READY에서 편집 화면(제목·본문·참고 자료)을 연다.
    banner_start = page.index("selected.row_state.kind === 'blocked'")
    banner = page[banner_start : page.index("</button>", banner_start)]
    assert "enterEditMode" in banner and "콘텐츠 수정" in banner
    assert ">제목</label>" in page
    assert "본문 (마크다운)" in page
    assert ">참고 자료</label>" in page
    assert "+ 참고 자료 추가" in page
    assert "'저장'" in page


@pytest.mark.parametrize("name", sorted(COPIES))
def test_every_quoted_control_in_the_copy_exists_on_the_content_screen(name):
    quoted = re.findall(r"“([^”]+)”", COPIES[name])
    page = _page()
    for label in quoted:
        assert label in page, f"{name}이(가) 콘텐츠 화면에 없는 “{label}”을(를) 말한다"
    if name == "operator_decides_action":
        assert quoted == ["콘텐츠 수정", "참고 자료 추가"]


@pytest.mark.parametrize("name", sorted(COPIES))
def test_the_copy_names_only_the_two_real_ways_out(name):
    copy = COPIES[name]
    for phrase in NONEXISTENT_ACTIONS:
        assert phrase not in copy, f"{name}이(가) 없는 조작 '{phrase}'을(를) 말한다"
    # (a) 글을 직접 뒷받침하는 공공·학술 기관 문서를 참고 자료로 넣기
    assert "공공·학술 기관 문서" in copy
    # (b) 제목·본문을 질환·검사 안내 글로 고치기
    assert "제목·본문을 질환·검사 안내 글로 고쳐 저장" in copy
