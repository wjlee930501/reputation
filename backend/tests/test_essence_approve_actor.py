import uuid

import pytest
from fastapi import HTTPException

from app.api.admin import essence as essence_api
from app.services.audit_log import (
    UNVERIFIED_ACTOR_PREFIX,
    reset_request_actor,
    set_request_actor,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, f"{UNVERIFIED_ACTOR_PREFIX}someone@example.com"])
async def test_approve_philosophy_rejects_an_unverified_actor_before_db_access(actor):
    body = essence_api.PhilosophyApprove(
        reviewed_by="request-body-reviewer",
        approval_note=None,
        confirm_evidence_reviewed=True,
    )
    token = set_request_actor(actor)
    try:
        with pytest.raises(HTTPException) as exc:
            await essence_api.approve_philosophy(
                uuid.uuid4(), uuid.uuid4(), body, db=None
            )
    finally:
        reset_request_actor(token)

    assert exc.value.status_code == 403
    assert "다시 로그인" in exc.value.detail
