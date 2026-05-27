import json
import os

os.environ.setdefault("ADMIN_SECRET_KEY", "test-admin-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///tmp/reputation-test.db")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///tmp/reputation-test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

import pytest  # noqa: E402

from app.services.content_engine import _parse_json_response, _validate_body_length  # noqa: E402


def test_parse_json_response_accepts_fenced_json():
    raw = """```json
{"title":"제목","body":"본문","meta_description":"요약"}
```"""

    parsed = _parse_json_response(raw, json_module=json)

    assert parsed["title"] == "제목"
    assert parsed["body"] == "본문"


def test_parse_json_response_extracts_surrounded_object():
    raw = 'Here is the JSON:\n{"title":"제목","body":"본문"}\nDone.'

    parsed = _parse_json_response(raw, json_module=json)

    assert parsed == {"title": "제목", "body": "본문"}


def test_validate_body_length_accepts_expert_blog_length():
    _validate_body_length("## 제목\n" + ("본문입니다. " * 360))


def test_validate_body_length_rejects_short_body():
    with pytest.raises(ValueError, match="too short"):
        _validate_body_length("짧은 본문")


def test_validate_body_length_rejects_runaway_body():
    with pytest.raises(ValueError, match="too long"):
        _validate_body_length("긴 본문입니다. " * 900)
