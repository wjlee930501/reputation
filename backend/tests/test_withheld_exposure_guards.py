"""비공개(보존) 글은 AI 노출 작업의 '빈 슬롯'이 아니다.

WITHHELD 글은 공개됐던 판을 그대로 들고 있다. 공개 글과 같이, 노출 작업에 연결하거나
새 콘텐츠 가이드를 붙일 슬롯으로 고르면 안 된다 — 고르면 그 작업은 다시 쓰이지 않을
글을 기다리고, 보존한 판에 새 가이드가 덮인다.
"""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.admin import exposure_actions as exposure_actions_api
from app.models.content import ContentStatus, ContentType
from app.services.exposure_content_linker import link_content_to_exposure_action


async def test_withheld_content_cannot_be_linked_to_an_exposure_action():
    action = SimpleNamespace(id=uuid.uuid4(), action_type="CONTENT", linked_content_id=None)
    item = SimpleNamespace(id=uuid.uuid4(), status=ContentStatus.WITHHELD, exposure_action_id=None)

    with pytest.raises(HTTPException) as caught:
        await link_content_to_exposure_action(None, action=action, item=item)

    assert caught.value.status_code == 409
    assert item.exposure_action_id is None


class _CapturingDB:
    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return None

        return _Result()


async def test_available_slot_search_skips_withheld_content():
    db = _CapturingDB()

    await exposure_actions_api._find_available_content_slot(
        db, uuid.uuid4(), date(2026, 9, 1), date(2026, 9, 30), ContentType.FAQ
    )

    [stmt] = db.statements
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "content_items.status NOT IN ('PUBLISHED', 'WITHHELD')" in sql
