"""배포 직후 예약 작업을 기다리지 않고 지금 한 번 보내는 운영 도구 — 가상 큐만 쓴다."""

import pytest

from app.core.celery_app import celery_app
from app.utils import run_beat_entry


class _Sent:
    id = "fictional-task-id"


def test_sends_the_entry_exactly_like_beat(monkeypatch):
    calls = []
    monkeypatch.setattr(
        celery_app, "send_task", lambda name, **kwargs: calls.append((name, kwargs)) or _Sent()
    )

    task_id = run_beat_entry.send_now("post-publish-ai-review")

    entry = celery_app.conf.beat_schedule["post-publish-ai-review"]
    assert task_id == "fictional-task-id"
    assert calls == [(entry["task"], {"args": (), "kwargs": {}, **entry["options"]})]


def test_unknown_entry_is_refused_with_the_known_names(monkeypatch):
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: pytest.fail("must not send"))

    with pytest.raises(SystemExit) as exc:
        run_beat_entry.main(["no-such-entry"])

    assert "post-publish-ai-review" in str(exc.value)
