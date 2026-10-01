"""공개 재검수 백필: 필수 문구 면제는 현재 승인본 문구만 근거로 한다(PostgreSQL).

공개 글의 저장 가이드(brief)에 승인본이 바뀌기 전 2cm 문구가 남아 있어도, 백필이 넘기는 그
가이드는 면제 근거가 아니다. 현재 승인본(1cm) 문장 지적만 기록용이 되고 2cm 문장의 HARD는
차단으로 남아 `blocked`로 끝나야 한다. 실제 독립 검수 판정을 태우고 공급자만 가짜로 바꾼다.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.essence import HospitalContentPhilosophy
from app.models.hospital import Hospital
from app.models.operations import OperationRun
from app.services import content_ai_review
from app.services import content_public_review_backfill as backfill
from app.services.content_publication import public_candidate_review_safe
from tests.integration.test_content_public_review_backfill_postgres import (
    _allow,
    _refresh,
    _seed,
)
from tests.must_use_review_support import (
    APPROVED_1CM,
    LEAD,
    STALE_BRIEF_2CM,
    approved_and_stale_verdict,
    findings_by_quote,
    install_fake_reviewer,
)
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


@pytest.fixture
def committed_db(pg_engine):
    """백필은 공급자 호출 전후로 커밋하므로 실제 커밋 세션을 쓰고 끝에 지운다."""

    db = Session(pg_engine, expire_on_commit=False)
    hospital_ids: list[uuid.UUID] = []
    try:
        yield db, hospital_ids
    finally:
        db.rollback()
        if hospital_ids:
            db.execute(delete(OperationRun).where(OperationRun.hospital_id.in_(hospital_ids)))
            db.execute(delete(Hospital).where(Hospital.id.in_(hospital_ids)))
            db.commit()
        db.close()


def test_backfill_keeps_hard_on_a_stale_brief_must_use_sentence(committed_db, monkeypatch) -> None:
    db, tracked = committed_db
    seed = _seed(db, tracked, review_status="REVISE")
    philosophy = _refresh(db, HospitalContentPhilosophy, seed.philosophy.id)
    philosophy.must_use_messages = [APPROVED_1CM]
    item = _refresh(db, ContentItem, seed.item.id)
    item.body = f"{LEAD} {APPROVED_1CM} {STALE_BRIEF_2CM}"
    # 승인본이 1cm로 바뀌기 전에 저장된 가이드. 옛 2cm 문구가 남아 있다.
    item.content_brief = {**item.content_brief, "must_use_messages": [STALE_BRIEF_2CM]}
    db.commit()
    calls = install_fake_reviewer(monkeypatch, [approved_and_stale_verdict()])

    result = backfill.run_content_public_review_backfill(
        db,
        content_ids={seed.item.id},
        dry_run=False,
        reserve=_allow,
        reviewer=content_ai_review.review_generated_content,
    )

    assert len(calls) == 1
    assert (result.blocked, result.cleared) == (1, 0)
    item = _refresh(db, ContentItem, seed.item.id)
    stored = item.essence_check_summary["ai_review"]
    assert stored["blocking"] is True
    assert public_candidate_review_safe(item) is False
    by_quote = findings_by_quote(stored)
    assert by_quote[APPROVED_1CM]["target"] == "MUST_USE_MESSAGE"
    assert by_quote[APPROVED_1CM]["severity"] == "SOFT"
    assert by_quote[STALE_BRIEF_2CM]["target"] == "CANDIDATE_TEXT"
    assert by_quote[STALE_BRIEF_2CM]["severity"] == "HARD"
