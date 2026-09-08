"""월 표의 행 상태는 한 함수가 정한다 — 공개 사이트와 같은 판정 위에 차단 링크만 얹는다."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.models.content import ContentStatus, ContentType
from app.services.content_row_state import ROW_STATE_LABELS, RowState, content_row_state
from app.services.content_visibility import PublicVisibility


def _item(**over):
    base = dict(
        status=ContentStatus.PUBLISHED,
        content_type=ContentType.DISEASE,
        title="제목",
        body="본문",
        scheduled_date=date(2026, 9, 20),
        published_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


VISIBLE = PublicVisibility(visible=True, blockers=())
WITHHELD = PublicVisibility(visible=False, blockers=("IMAGE_NOT_CERTIFIED",))


def test_published_rows_follow_the_public_visibility():
    assert content_row_state(
        _item(), VISIBLE, compliance_blockers=(), blocked_link=None, today=date(2026, 9, 9)
    ) == RowState("public", None, None)
    withheld = content_row_state(
        _item(), WITHHELD, compliance_blockers=(), blocked_link=None, today=date(2026, 9, 9)
    )
    assert withheld.kind == "withheld" and withheld.reason == "대표 이미지 재인증 대기"


def test_drafts_are_scheduled_generating_or_blocked():
    ok = _item(status=ContentStatus.DRAFT, published_at=None)
    assert (
        content_row_state(
            ok, VISIBLE, compliance_blockers=(), blocked_link=None, today=date(2026, 9, 9)
        ).kind
        == "scheduled"
    )
    empty = _item(status=ContentStatus.DRAFT, published_at=None, title=None, body=None)
    gen = content_row_state(
        empty,
        VISIBLE,
        compliance_blockers=("본문 생성이 필요합니다.",),
        blocked_link=None,
        today=date(2026, 9, 9),
    )
    assert gen == RowState("generating", "발행 전날 23:00 자동 생성", None)
    blocked = content_row_state(
        ok,
        VISIBLE,
        compliance_blockers=("권위 있는 참고 자료가 1개 이상 필요합니다.",),
        blocked_link={"kind": "run", "href": "/operations/x"},
        today=date(2026, 9, 9),
    )
    assert (
        blocked.kind == "blocked"
        and blocked.reason.startswith("권위")
        and blocked.link == {"kind": "run", "href": "/operations/x"}
    )


def test_rejected_and_cancelled():
    assert content_row_state(
        _item(status=ContentStatus.REJECTED, title=None),
        VISIBLE,
        compliance_blockers=("본문 생성이 필요합니다.",),
        blocked_link=None,
        today=date(2026, 9, 9),
    ) == RowState("generating", "야간 재생성 대기", None)
    assert content_row_state(
        _item(status=ContentStatus.CANCELLED),
        VISIBLE,
        compliance_blockers=(),
        blocked_link=None,
        today=date(2026, 9, 9),
    ) == RowState("closed", "종료됨", None)


def test_incident_link_wins_over_generic_blocker_text():
    item = _item(status=ContentStatus.DRAFT, published_at=None, title=None, body=None)
    state = content_row_state(
        item,
        VISIBLE,
        compliance_blockers=("본문 생성이 필요합니다.",),
        blocked_link={
            "kind": "incident",
            "href": "/operations/hospitals/h/incidents/i",
            "next_action": "재생성을 다시 시도하세요",
        },
        today=date(2026, 9, 9),
    )
    assert state.kind == "blocked" and state.reason == "재생성을 다시 시도하세요"


def test_withheld_published_row_keeps_the_blocked_link():
    """공개 보류 글도 조치 링크가 있으면 같이 내려간다 — 사유만으로는 어디로 갈지 모른다."""
    link = {"kind": "run", "href": "/operations/hospitals/h/runs/r", "next_action": None}
    state = content_row_state(
        _item(), WITHHELD, compliance_blockers=(), blocked_link=link, today=date(2026, 9, 9)
    )
    assert state.kind == "withheld" and state.link == link


def test_withheld_row_link_is_a_routable_operations_address():
    """공개 보류 행의 링크는 실제로 열리는 운영 센터 주소여야 한다."""
    link = {
        "kind": "run",
        "href": "/operations?queue=incidents&hospital_id=h",
        "next_action": None,
    }
    state = content_row_state(
        _item(), WITHHELD, compliance_blockers=(), blocked_link=link, today=date(2026, 9, 9)
    )
    assert state.link["href"].startswith("/operations?queue=incidents&hospital_id=")


def test_overdue_empty_draft_is_blocked_not_generating():
    """발행일이 지난 빈 초안은 "생성 중"이 아니다 — 야간 생성이 이미 지나갔다."""
    late = _item(
        status=ContentStatus.DRAFT,
        published_at=None,
        title=None,
        body=None,
        scheduled_date=date(2026, 9, 5),
    )
    state = content_row_state(
        late,
        VISIBLE,
        compliance_blockers=("본문 생성이 필요합니다.",),
        blocked_link=None,
        today=date(2026, 9, 9),
    )
    assert state == RowState("blocked", "발행일이 지났지만 아직 생성되지 않았습니다.", None)


def test_overdue_draft_is_not_called_scheduled():
    """발행일이 지났는데 아직 초안이면 "예정"이 아니다 — 08:00 자동 공개가 실패한 것이다."""
    late = _item(
        status=ContentStatus.DRAFT, published_at=None, scheduled_date=date(2026, 9, 5)
    )
    state = content_row_state(
        late, VISIBLE, compliance_blockers=(), blocked_link=None, today=date(2026, 9, 9)
    )
    assert state.kind == "blocked" and state.reason == "발행일이 지났지만 아직 공개되지 않았습니다."


def test_labels_cover_every_kind():
    assert set(ROW_STATE_LABELS) == {
        "public",
        "withheld",
        "scheduled",
        "generating",
        "blocked",
        "closed",
    }
