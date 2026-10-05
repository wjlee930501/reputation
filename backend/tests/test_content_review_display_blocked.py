"""Admin 목록의 검수 표시는 실제로 막힌 글을 '자동 발행 대기'라고 부르지 않는다(PR-B 3).

2026-10-03 마포 목록(47b36df6·da910e02·69753c89): `row_state`는 차단 "독립 검수 지적이 해결되지
않았습니다."인데 `display.review`는 같은 글을 `자동 발행 대기`·`publishable=True`로 말했다. 제목·본문이
있고 운영 기준 검사가 ALIGNED이기만 하면 그렇게 표시했기 때문이다. 이제 준법 요약
(`compliance.publishable`)이 거짓이면 `자동 발행 차단`이고, 사유는 행 상태와 같은 문장(준법 차단 사유를
" · "로 이은 것, 차단 링크가 있으면 그 조치 문장)이다. 다른 상태의 표시는 그대로다.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models.content import ContentStatus
from tests.test_content_visibility import _published, _serialize

READY_LABEL = "자동 발행 대기"
BLOCKED_LABEL = "자동 발행 차단"


def _draft(**overrides):
    fields = {"status": ContentStatus.DRAFT, "published_at": None, "published_by": None}
    fields.update(overrides)
    return _published(**fields)


def _serialize_row(item, philosophy_id, *, blocked_link=None):
    from app.api.admin import content as admin_content

    return admin_content._serialize_item(
        item,
        full=True,
        public_philosophy_id=philosophy_id,
        hospital_serving=True,
        blocked_link=blocked_link,
    )


def _unresolved_review():
    return {
        "ai_review": {
            "status": "REVISE",
            "blocking": True,
            "schema_version": 2,
            "candidate_sha256": "0" * 64,
            "findings": [
                {"severity": "UNCERTAIN", "kind": "MEDICAL_SAFETY", "message": "근거 확인 필요"}
            ],
        }
    }


BLOCKED_SHAPES = {
    "unresolved_ai_review": dict(essence_check_summary=_unresolved_review()),
    "review_unavailable": dict(
        essence_check_summary={"ai_review": {"status": "UNAVAILABLE", "unavailable_reason": "INVALID_RESPONSE"}}
    ),
    "image_not_certified": dict(image_policy_verified_at=None),
    "forbidden_expression": dict(meta_description="완치를 약속드립니다."),
    "missing_references": dict(references_list=[]),
}


def test_a_clean_draft_still_reads_as_waiting_for_automatic_publication():
    item, philosophy_id = _draft()

    serialized = _serialize_row(item, philosophy_id)

    assert serialized["compliance"]["publishable"] is True
    assert serialized["display"]["review"] == {
        "label": READY_LABEL,
        "reason": None,
        "publishable": True,
    }


@pytest.mark.parametrize("shape", sorted(BLOCKED_SHAPES))
def test_a_draft_the_gate_blocks_reads_as_blocked_with_the_rows_reason(shape):
    item, philosophy_id = _draft(**BLOCKED_SHAPES[shape])

    serialized = _serialize_row(item, philosophy_id)

    compliance = serialized["compliance"]
    assert compliance["publishable"] is False and compliance["blockers"]
    review = serialized["display"]["review"]
    assert review["label"] == BLOCKED_LABEL
    assert review["publishable"] is False
    assert review["reason"] == " · ".join(compliance["blockers"])
    assert review["reason"] == serialized["row_state"]["reason"]


def test_the_mapo_incident_shape_no_longer_says_waiting():
    """47b36df6 형태 — 행 상태는 '독립 검수 지적이 해결되지 않았습니다.'로 막혀 있다."""

    item, philosophy_id = _draft(essence_check_summary=_unresolved_review())

    serialized = _serialize_row(item, philosophy_id)

    assert serialized["row_state"]["kind"] == "blocked"
    assert "독립 검수 지적이 해결되지 않았습니다." in serialized["row_state"]["reason"]
    review = serialized["display"]["review"]
    assert review["label"] != READY_LABEL
    assert "독립 검수 지적이 해결되지 않았습니다." in review["reason"]


def test_a_blocked_draft_with_an_operator_link_shows_the_operator_next_action():
    item, philosophy_id = _draft(essence_check_summary=_unresolved_review())
    link = {
        "kind": "incident",
        "href": f"/operations?incident={uuid.uuid4()}",
        "next_action": "지적된 사실을 병원 정보 탭의 승인 자료에 채우세요.",
    }

    serialized = _serialize_row(item, philosophy_id, blocked_link=link)

    review = serialized["display"]["review"]
    assert review["label"] == BLOCKED_LABEL
    assert review["publishable"] is False
    assert review["reason"] == link["next_action"] == serialized["row_state"]["reason"]


@pytest.mark.parametrize("shape", sorted(BLOCKED_SHAPES))
def test_the_display_never_calls_a_non_publishable_row_publishable(shape):
    item, philosophy_id = _draft(**BLOCKED_SHAPES[shape])

    serialized = _serialize_row(item, philosophy_id)

    assert not (
        serialized["display"]["review"]["publishable"] and not serialized["compliance"]["publishable"]
    )


# ── 보존: 다른 상태의 표시는 그대로다 ─────────────────────────────────────────────────


def test_an_ungenerated_slot_keeps_its_label():
    item, philosophy_id = _draft(title=None, body=None, scheduled_date=date(2099, 1, 1))

    review = _serialize_row(item, philosophy_id)["display"]["review"]

    assert review == {"label": "생성 전", "reason": "야간 자동 생성 대기", "publishable": False}


@pytest.mark.parametrize(
    ("essence_status", "reason"),
    [
        ("NEEDS_ESSENCE_REVIEW", "운영 기준 재검토 필요"),
        ("MISSING_APPROVED_PHILOSOPHY", "승인된 운영 기준 없음"),
        (None, "운영 기준 미검수"),
    ],
)
def test_an_unaligned_draft_keeps_its_essence_reason(essence_status, reason):
    item, philosophy_id = _draft(essence_status=essence_status)

    review = _serialize_row(item, philosophy_id)["display"]["review"]

    assert review == {"label": BLOCKED_LABEL, "reason": reason, "publishable": False}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ContentStatus.REJECTED, {"label": "반려됨", "reason": "야간 재생성 대기", "publishable": False}),
        (ContentStatus.CANCELLED, {"label": "종료됨", "reason": "중복·노후 슬롯", "publishable": False}),
    ],
)
def test_rejected_and_cancelled_keep_their_labels(status, expected):
    item, philosophy_id = _draft(status=status)

    review = _serialize_row(item, philosophy_id)["display"]["review"]

    assert review == expected


def test_withheld_keeps_its_label():
    from app.api.admin.content import WITHHELD_DISPLAY_LABEL

    item, philosophy_id = _draft(status=ContentStatus.WITHHELD)

    review = _serialize_row(item, philosophy_id)["display"]["review"]

    assert review["label"] == WITHHELD_DISPLAY_LABEL and review["publishable"] is False


def test_published_rows_keep_their_labels():
    item, philosophy_id = _published(image_policy_verified_at=None, image_content_hash=None)

    review = _serialize(item, philosophy_id)["display"]["review"]

    assert review["label"] == "공개 보류" and review["publishable"] is False
