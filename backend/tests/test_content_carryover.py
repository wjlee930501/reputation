"""월말 반려 carry-over — 직렬화 노출 경계 + 아침 Slack '전월 이월' 표시.

carried_over_from은 내부 운영 데이터: Admin 직렬화에는 포함하고,
공개(/site) 직렬화에는 절대 포함하지 않는다.
"""
from datetime import date, datetime
from types import SimpleNamespace

from app.api.admin import content as content_api
from app.api.public import site as public_site
from app.models.content import ContentStatus
from app.services import notifier
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED


def _admin_item(carried_over_from=None):
    return SimpleNamespace(
        id="content-id",
        content_type="FAQ",
        sequence_no=1,
        total_count=16,
        title="무릎이 아플 때",
        body="본문",
        meta_description="요약",
        image_url=None,
        scheduled_date=date(2026, 7, 1),
        carried_over_from=carried_over_from,
        status=ContentStatus.DRAFT,
        generated_at=None,
        published_at=None,
        published_by=None,
        body_updated_at=None,
        references_list=[{"title": "근거", "url": "https://example.com"}],
        faq_question=None,
        faq_answer_summary=None,
        content_philosophy_id=None,
        query_target_id=None,
        exposure_action_id=None,
        content_brief=None,
        brief_status=None,
        brief_approved_at=None,
        brief_approved_by=None,
        essence_status=ESSENCE_STATUS_ALIGNED,
        essence_check_summary=None,
        image_prompt=None,
    )


def test_admin_serializer_includes_carried_over_from():
    serialized = content_api._serialize_item(_admin_item(carried_over_from=date(2026, 6, 30)))
    assert serialized["carried_over_from"] == "2026-06-30"

    serialized_none = content_api._serialize_item(_admin_item())
    assert serialized_none["carried_over_from"] is None


def test_public_serializer_never_exposes_carried_over_from():
    """공개(/site) 응답에는 내부 운영 데이터를 노출하지 않는다."""
    item = SimpleNamespace(
        id="content-id",
        content_type="FAQ",
        title="무릎이 아플 때",
        meta_description="요약",
        image_url=None,
        scheduled_date=date(2026, 7, 1),
        published_at=datetime(2026, 7, 1, 8, 0, 0),
        body_updated_at=None,
        references_list=[],
        faq_question=None,
        faq_answer_summary=None,
        body="본문",
        carried_over_from=date(2026, 6, 30),  # 값이 있어도
    )

    serialized = public_site._serialize_item(item, full=True)

    assert "carried_over_from" not in serialized


async def test_draft_ready_notification_marks_carried_over(monkeypatch):
    sent = []

    async def fake_send(*, text, blocks=None):
        sent.append({"text": text, "blocks": blocks})
        return True

    monkeypatch.setattr(notifier, "_send", fake_send)

    await notifier.notify_content_draft_ready(
        hospital_name="장편한외과의원",
        sequence_no=1,
        total_count=16,
        content_type="FAQ",
        scheduled_date="2026-07-01",
        admin_url="https://admin.example.com/x",
        carried_over=True,
    )
    await notifier.notify_content_draft_ready(
        hospital_name="장편한외과의원",
        sequence_no=2,
        total_count=16,
        content_type="FAQ",
        scheduled_date="2026-07-02",
        admin_url="https://admin.example.com/y",
    )

    carried_text = sent[0]["blocks"][0]["text"]["text"]
    assert "(전월 이월 — 우선 검토)" in carried_text
    assert "발행 예정일: 2026-07-01 (전월 이월 — 우선 검토)" in carried_text

    normal_text = sent[1]["blocks"][0]["text"]["text"]
    assert "전월 이월" not in normal_text
    assert "전월 이월" not in sent[1]["text"]
