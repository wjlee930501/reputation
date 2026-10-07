"""FLAGGED 글에 사람이 '공개 내용 확인'을 기록하면 표시를 걷고 인시던트를 닫는다."""

import uuid

from app.api.admin import content as content_api
from app.services import ops_incident_alerts
from tests.test_content_brief import _published_certified_item, _ReviewFakeDB


async def test_manual_review_clears_flag_and_resolves_incident(monkeypatch):
    hospital_id, item_id = uuid.uuid4(), uuid.uuid4()
    item = _published_certified_item(id=item_id, hospital_id=hospital_id)
    item.essence_check_summary = {
        "keep": 1,
        "post_publish_ai_review": {"status": "FLAGGED", "findings": ["x"]},
    }
    recovered = []

    async def fake_get_content(db, *_):
        return item

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_philosophy_id(db, _hospital_id):
        return None

    async def fake_recover(**kwargs):
        recovered.append(kwargs)
        return True

    monkeypatch.setattr(content_api, "_get_content", fake_get_content)
    monkeypatch.setattr(content_api, "write_audit_log", fake_audit)
    monkeypatch.setattr(content_api, "default_actor", lambda: "operator@example.com")
    monkeypatch.setattr(content_api, "get_public_approved_philosophy_id", fake_philosophy_id)
    monkeypatch.setattr(
        content_api,
        "assess_public_visibility",
        lambda *_: content_api.PublicVisibility(visible=True, blockers=()),
    )
    monkeypatch.setattr(ops_incident_alerts, "recover_ops_incident", fake_recover)

    await content_api.complete_post_publish_review(
        hospital_id, item_id, content_api.PostPublishReviewBody(), db=_ReviewFakeDB()
    )

    assert item.post_publish_reviewed_by == "operator@example.com"
    assert item.essence_check_summary == {"keep": 1}
    assert len(recovered) == 1
    assert recovered[0]["object_id"] == str(item_id)
    assert recovered[0]["pipeline"] == "post_publish_ai_review"
