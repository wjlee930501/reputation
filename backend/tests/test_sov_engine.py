import json
from types import SimpleNamespace

import pytest

from app.services import sov_engine


class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)


class _FakeCompletions:
    async def create(self, **kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(
            choices=[
                _FakeChoice(
                    json.dumps(
                        {
                            "competitors": [
                                {"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1},
                            ]
                        }
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_parse_competitors_accepts_json_object_wrapper(monkeypatch):
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _FakeCompletions(),
    )

    parsed = await sov_engine._parse_competitors(["경쟁병원"], "경쟁병원이 먼저 언급되었습니다.")

    assert parsed == [{"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1}]
