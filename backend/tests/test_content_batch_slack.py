import json
import uuid
from typing import TypedDict

from app.models.operations import JSONValue
from app.services import notifier


class CapturedMessage(TypedDict):
    text: str
    blocks: list[dict[str, JSONValue]] | None


async def test_nightly_batch_slack_has_one_exact_operator_action_without_jargon(
    monkeypatch,
) -> None:
    captured: CapturedMessage = {"text": "", "blocks": None}

    async def capture_send(text: str, blocks: list[dict[str, JSONValue]] | None = None) -> bool:
        captured["text"] = text
        captured["blocks"] = blocks
        return True

    hospital_id = uuid.UUID("10000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(notifier, "_send", capture_send)
    monkeypatch.setattr(notifier.settings, "ADMIN_BASE_URL", "https://admin.example.test")

    sent = await notifier.notify_content_batch_summary(
        hospital_id=hospital_id,
        hospital_name="장편한외과의원<script>",
        generated=1,
        failed=1,
        scheduled_date="2026-08-11",
        skipped=1,
        cost_blocked=1,
        image_missing=1,
    )

    assert sent is True
    payload = json.dumps(captured, ensure_ascii=False)
    assert all(label in payload for label in ("무슨 문제인지", "고객 영향", "지금 할 일", "처리 기한"))
    assert all(term not in payload for term in ("비용 가드", "스킵", "Celery", "Redis", "FAILED", "UNKNOWN", "<script>"))
    actions = [block for block in (captured["blocks"] or []) if block.get("type") == "actions"]
    assert len(actions) == 1
    assert actions[0]["elements"][0]["url"] == (
        f"https://admin.example.test/hospitals/{hospital_id}/content"
    )


async def test_nightly_batch_slack_skips_an_empty_summary(monkeypatch) -> None:
    called = False

    async def capture_send(text: str, blocks: list[dict[str, JSONValue]] | None = None) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(notifier, "_send", capture_send)

    sent = await notifier.notify_content_batch_summary(
        hospital_id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
        hospital_name="장편한외과의원",
        generated=0,
        failed=0,
        scheduled_date="2026-08-11",
    )

    assert sent is False
    assert called is False
