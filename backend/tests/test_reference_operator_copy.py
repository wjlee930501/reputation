"""참고 자료 보류·거절 문구는 Admin에 실제로 있는 조작만 말한다(리뷰 3차 F5, 4차 F5 잔여).

예전 문구의 '해당 항목을 종료'는 막다른 길이었다 — 콘텐츠 화면에는 슬롯 종료 버튼이 없고
(`admin/lib/content-page-contract.test.ts`가 일반 '/cancel'·'콘텐츠 항목 종료'를 금지한다), 운영 센터의
조작은 다시 시도·해결·담당 지정뿐이다. 문구가 이름 붙이는 조작(“콘텐츠 수정”·“참고 자료 추가”,
제목·본문 편집, 저장)이 콘텐츠 화면 소스에 실제로 있는지, 없는 조작을 말하지 않는지 확인한다.

Admin 소스(`admin/`)가 없는 backend 전용 체크아웃·컨테이너에서는 건너뛴다. CI(`CI` 설정)는 저장소
전체를 체크아웃하고 backend/에서 pytest를 돌리므로(.github/workflows/ci.yml) 거기서 없으면 실패다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app.api.admin.content import CURATED_REFERENCE_NOT_ALLOWED_MESSAGE
from app.workers.generation_incident_control import (
    REFERENCE_REJECTION_OPERATOR_ACTION,
    REFERENCES_OPERATOR_DECIDES_ACTION,
)

_REPO = Path(__file__).resolve().parents[2]
_CONTENT_PAGE = _REPO / "admin" / "app" / "hospitals" / "[id]" / "content" / "page.tsx"
_CONTENT_CONTRACT = _REPO / "admin" / "lib" / "content-page-contract.test.ts"

COPIES = {
    "operator_decides_action": REFERENCES_OPERATOR_DECIDES_ACTION,
    "patch_422_message": CURATED_REFERENCE_NOT_ALLOWED_MESSAGE,
    "reference_rejection_action": REFERENCE_REJECTION_OPERATOR_ACTION,
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


def require_admin_source(paths=(_CONTENT_PAGE, _CONTENT_CONTRACT), *, environ=os.environ) -> None:
    """Admin 소스가 없으면 로컬은 건너뛰고, CI에서는 실패한다(검사가 조용히 사라지지 않게)."""

    missing = [str(path) for path in paths if not path.is_file()]
    if not missing:
        return
    if environ.get("CI"):
        pytest.fail(f"CI인데 Admin 소스가 없다 — 저장소 전체 체크아웃이 필요하다: {missing}")
    pytest.skip(f"Admin 소스가 없는 backend 전용 체크아웃: {missing}")


@pytest.fixture(autouse=True)
def _admin_source_present(request):
    if "admin_source_guard" not in request.node.name:
        require_admin_source()


def _guard_outcome(paths, environ) -> str:
    # skip·fail은 BaseException이다 — 그대로 새면 이 테스트 자체가 건너뛰기로 끝나 뮤턴트가 산다.
    try:
        require_admin_source(paths, environ=environ)
    except pytest.fail.Exception:
        return "fail"
    except pytest.skip.Exception:
        return "skip"
    return "ok"


def test_admin_source_guard_fails_under_ci_and_skips_locally(tmp_path):
    absent = (tmp_path / "admin" / "page.tsx",)

    assert _guard_outcome(absent, {"CI": "true"}) == "fail"
    assert _guard_outcome(absent, {}) == "skip"
    assert _guard_outcome((Path(__file__),), {"CI": "true"}) == "ok"  # 있으면 그대로 진행


def _page() -> str:
    return _CONTENT_PAGE.read_text(encoding="utf-8")


def test_the_content_screen_really_has_no_close_or_regenerate_action():
    """전제 — 콘텐츠 화면 계약이 항목 종료·재생성·발행일 이동을 금지한다."""

    contract = _CONTENT_CONTRACT.read_text(encoding="utf-8")
    for fragment in ("'/cancel'", "'콘텐츠 항목 종료'", "'즉시 재생성'", "'발행일 옮기기'"):
        assert fragment in contract
    page = _page()
    assert "/candidate/cancel" in page
    without_candidate_cancel = page.replace("/candidate/cancel", "")
    assert "/cancel" not in without_candidate_cancel
    assert "콘텐츠 항목 종료" not in page


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
    if name != "patch_422_message":
        assert quoted == ["콘텐츠 수정", "참고 자료 추가"]


# 문구마다 안내해야 하는 실제 조작. 보류(진료비·병원 선택)와 422는 둘 중 하나, 생성 거절은 본문이
# 없는 슬롯에도 참이어야 해서 "쓰거나 고치고 + 문서를 넣어 저장" 한 가지다.
REQUIRED_GUIDANCE = {
    "operator_decides_action": (
        "공공·학술 기관 문서",  # (a) 글을 직접 뒷받침하는 문서를 참고 자료로 넣기
        "제목·본문을 질환·검사 안내 글로 고쳐 저장",  # (b) 질환·검사 안내 글로 고치기
        # (b)를 문서 없이 저장하면 진료비·병원 선택 글이 아니게 되어 평범한 참고자료 보류가 되고,
        # 저장 본문 수리(`BODY_REPAIR_CODES`)가 작가에게 돌려 본문을 다시 쓴다.
        "질환·검사 안내 글로 고쳐 참고 자료 없이 저장하면 자동 복구가 참고 자료를 찾으며 본문을 "
        "다시 쓸 수 있습니다",
        # 진료비·병원 선택 글로 두는 동안 다시 쓰지 않는 것도 생성 조건이 바뀌기 전까지다 —
        # 새 운영 기준 승인은 옛 기준으로 쓴 본문을 한 번 다시 쓴다(`_generate_single_content_item`).
        "진료비·병원 선택 글로 두면 자동 복구는 운영 기준이 새로 승인되는 등 생성 조건이 바뀌기 "
        "전에는 이 글을 다시 쓰지 않습니다",
    ),
    "patch_422_message": (
        "공공·학술 기관 문서",
        "제목·본문을 질환·검사 안내 글로 고쳐 저장",
    ),
    "reference_rejection_action": (
        "제목·본문을 질환·검사 안내 글로 쓰거나 고치고",
        "공공·학술 기관 문서를 “참고 자료 추가”로 넣어 저장하세요",
        # 문서 없이 저장하면 복구 스윕이 작가에게 돌려 본문을 다시 쓴다.
        "참고 자료 없이 저장하면 자동 복구가 참고 자료를 찾으며 본문을 다시 쓸 수 있습니다",
    ),
}
# 생성 거절 문구를 사람이 보는 때는 자동 재시도가 끝난 뒤다 — 재시도를 약속하지 않는다.
REFERENCE_REJECTION_FORBIDDEN = ("다음 자동 재시도", "넣지 않아도")


@pytest.mark.parametrize("name", sorted(COPIES))
def test_the_copy_names_only_the_real_ways_out(name):
    copy = COPIES[name]
    for phrase in NONEXISTENT_ACTIONS:
        assert phrase not in copy, f"{name}이(가) 없는 조작 '{phrase}'을(를) 말한다"
    for phrase in REQUIRED_GUIDANCE[name]:
        assert phrase in copy, f"{name}이(가) '{phrase}'을(를) 안내하지 않는다"
    if name == "reference_rejection_action":
        for phrase in REFERENCE_REJECTION_FORBIDDEN:
            assert phrase not in copy, f"{name}이(가) 사실이 아닌 '{phrase}'을(를) 말한다"
