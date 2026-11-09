"""Admin 콘텐츠 방향 API — 저장·검사·감사·미리보기."""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import content_focus as api
from app.models.audit import AdminAuditLog
from app.models.content import ContentStatus
from app.models.hospital import Hospital


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, hospital, *, targets=(), month_items=((), ())):
        self.hospital = hospital
        self.targets = list(targets)
        self.month_items = [list(rows) for rows in month_items]
        self.added: list = []
        self.commits = 0
        self._month_call = 0

    async def get(self, model, _object_id):
        return self.hospital if model is Hospital else None

    async def execute(self, stmt):
        if "ai_query_targets" in str(stmt):
            return _Result(self.targets)
        rows = self.month_items[self._month_call % len(self.month_items)]
        self._month_call += 1
        return _Result(rows)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _item):
        return None


def _hospital(focus=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        region=["하남시"],
        specialties=[],
        treatments=[],
        keywords=[],
        hero_specialties=[],
        content_focus_topics=focus if focus is not None else [],
    )


def _target(name, **fields):
    base = dict(
        id=uuid.uuid4(),
        name=name,
        treatment=None,
        condition_or_symptom=None,
        specialty=None,
        region_terms=["하남시"],
        variants=[],
    )
    base.update(fields)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_lock(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(api, "acquire_hospital_advisory_lock", _noop)
    monkeypatch.setattr(api, "_kst_today", lambda: date(2026, 11, 10))


@pytest.mark.asyncio
async def test_save_stores_versioned_focus_and_writes_audit():
    hospital = _hospital(["정형외과"])
    targets = [
        _target("위내시경 받으려는데 하남시 어느 병원으로 가야 해?", condition_or_symptom="위내시경"),
        _target("대장내시경 비용 알려줘", treatment="대장내시경"),
        _target("하남 진료를 받으려는데 하남시 어느 병원으로 가야 해?", condition_or_symptom="하남"),
        _target("간질환 진료 병원", condition_or_symptom="간질환"),
    ]
    november = [
        SimpleNamespace(content_focus_topic="내시경 검사", status=ContentStatus.PUBLISHED, title="t", body="b",
                        query_target_id=uuid.uuid4(), scheduled_date=date(2026, 11, 2)),
        SimpleNamespace(content_focus_topic=None, status=ContentStatus.DRAFT, title=None, body=None,
                        query_target_id=None, scheduled_date=date(2026, 11, 20)),
    ]
    db = _FakeDB(hospital, targets=targets, month_items=(november, ()))
    body = api.ContentFocusUpdate(topics=["내시경 검사", "여의사"], share=50, from_month="2026-11", until_month="2026-12")

    payload = await api.set_content_focus(hospital.id, body, db)

    assert hospital.content_focus_topics == {
        "version": 1,
        "topics": ["내시경 검사", "여의사"],
        "share": 50,
        "from_month": "2026-11",
        "until_month": "2026-12",
    }
    assert db.commits == 1
    audit = [row for row in db.added if isinstance(row, AdminAuditLog)]
    assert len(audit) == 1 and audit[0].action == "set_content_focus"
    assert audit[0].detail["before"] is None and audit[0].detail["before_legacy"] == ["정형외과"]
    matches = {row["topic"]: row for row in payload["topic_matches"]}
    # 지역명 질문은 세지 않는다 — 글 주제로 고를 수 없는 질문이다.
    assert matches["내시경 검사"]["matching_questions"] == 2
    assert matches["여의사"]["matching_questions"] == 0
    assert payload["months"][0] == {
        "month": "2026-11", "active": True, "total": 2, "focused": 1, "quota": 1, "open_slots": 1,
    }
    assert payload["months"][1]["month"] == "2026-12"


@pytest.mark.asyncio
async def test_invalid_input_is_422_and_nothing_changes():
    hospital = _hospital()
    db = _FakeDB(hospital)
    body = api.ContentFocusUpdate(topics=["내시경"], share=40)
    with pytest.raises(HTTPException) as excinfo:
        await api.set_content_focus(hospital.id, body, db)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "CONTENT_FOCUS_INVALID"
    assert hospital.content_focus_topics == []
    assert db.commits == 0 and not db.added


@pytest.mark.asyncio
async def test_empty_topics_switch_focus_off():
    hospital = _hospital({"version": 1, "topics": ["내시경"], "share": 50, "from_month": None, "until_month": None})
    db = _FakeDB(hospital)
    payload = await api.set_content_focus(hospital.id, api.ContentFocusUpdate(topics=[], share=50), db)
    assert hospital.content_focus_topics == []
    assert payload["topics"] == []
    assert all(not month["active"] for month in payload["months"])


@pytest.mark.asyncio
async def test_preview_counts_questions_without_saving():
    hospital = _hospital()
    db = _FakeDB(hospital, targets=[_target("위내시경 병원", treatment="위내시경")])
    result = await api.preview_content_focus(hospital.id, api.ContentFocusPreview(topics=["내시경", " 내시경 "]), db)
    assert result == {"topic_matches": [{"topic": "내시경", "matching_questions": 1, "examples": ["위내시경 병원"]}]}
    assert hospital.content_focus_topics == [] and db.commits == 0
