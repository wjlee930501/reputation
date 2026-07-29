"""쿼리 타깃 변경은 측정 범위(=외부 API 비용, =월간 리포트 언급률의 분모)를 바꾼다.

admin_audit_logs는 트리거로 append-only 보호되는 컴플라이언스 산출물인데, 이 모듈 전체에
감사 호출이 하나도 없었다. 각 변경 지점이 감사 행을 남기는지 회귀로 고정한다.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.admin import query_targets as api
from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital
from app.schemas.query_target import (
    AIQueryTargetCreate,
    AIQueryTargetUpdate,
    AIQueryVariantCreate,
    AIQueryVariantUpdate,
)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else []


class _FakeDB:
    """query_targets 엔드포인트에 필요한 최소 AsyncSession 목."""

    def __init__(self, *, hospital=None, results=()):
        self.hospital = hospital
        self._results = list(results)
        self.added: list = []
        self.commits = 0

    async def get(self, model, _object_id):
        return self.hospital if model is Hospital else None

    async def execute(self, _stmt):
        return _Result(self._results.pop(0) if self._results else None)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def refresh(self, _item):
        return None


def _variant(target_id, **overrides):
    base = dict(
        id=uuid.uuid4(),
        query_target_id=target_id,
        query_text="강남 치질 병원 추천",
        platform="CHATGPT",
        language="ko",
        is_active=True,
        query_matrix_id=None,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _target(hospital_id, **overrides):
    target_id = overrides.pop("id", uuid.uuid4())
    base = dict(
        id=target_id,
        hospital_id=hospital_id,
        name="강남 치질 추천",
        target_intent="추천형",
        region_terms=["강남"],
        specialty=None,
        condition_or_symptom=None,
        treatment=None,
        decision_criteria=[],
        patient_language="ko",
        platforms=["CHATGPT"],
        competitor_names=[],
        priority="NORMAL",
        status="ACTIVE",
        target_month="2026-05",
        created_by="AE",
        updated_by=None,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        variants=[],
    )
    base.update(overrides)
    base["variants"] = base["variants"] or [_variant(target_id)]
    return SimpleNamespace(**base)


def _audit_rows(db) -> list[AdminAuditLog]:
    return [item for item in db.added if isinstance(item, AdminAuditLog)]


def _only_audit(db) -> AdminAuditLog:
    rows = _audit_rows(db)
    assert len(rows) == 1, f"expected exactly one audit row, got {len(rows)}"
    return rows[0]


@pytest.fixture
def hospital_id():
    return uuid.uuid4()


async def test_create_query_target_writes_audit(hospital_id):
    created = _target(hospital_id, name="강남 치질 추천")
    db = _FakeDB(hospital=SimpleNamespace(id=hospital_id), results=[created])
    body = AIQueryTargetCreate(
        name="강남 치질 추천",
        target_intent="추천형",
        priority="HIGH",
        platforms=["CHATGPT", "GEMINI"],
        target_month="2026-05",
    )

    await api.create_query_target(hospital_id, body=body, db=db)

    log = _only_audit(db)
    assert log.action == "create_query_target"
    assert log.hospital_id == hospital_id
    assert log.target_type == "ai_query_target"
    assert log.detail["name"] == "강남 치질 추천"
    assert log.detail["priority"] == "HIGH"
    assert log.detail["platforms"] == ["CHATGPT", "GEMINI"]


async def test_update_query_target_audits_before_and_after_values(hospital_id):
    target = _target(hospital_id, status="ACTIVE", priority="NORMAL")
    db = _FakeDB(results=[target, target])

    await api.update_query_target(
        hospital_id,
        target.id,
        body=AIQueryTargetUpdate(status="PAUSED", priority="HIGH"),
        db=db,
    )

    log = _only_audit(db)
    assert log.action == "update_query_target"
    assert log.target_id == str(target.id)
    # 분모가 언제 어떻게 좁아졌는지 대조하려면 변경 전 값이 필수다.
    assert log.detail["changed_fields"]["status"] == {"from": "ACTIVE", "to": "PAUSED"}
    assert log.detail["changed_fields"]["priority"] == {"from": "NORMAL", "to": "HIGH"}


async def test_archive_query_target_writes_audit(hospital_id):
    target = _target(hospital_id, status="ACTIVE")
    db = _FakeDB(results=[target, target])

    await api.archive_query_target(hospital_id, target.id, db=db)

    log = _only_audit(db)
    assert log.action == "archive_query_target"
    assert log.detail["from_status"] == "ACTIVE"
    assert log.detail["to_status"] == "ARCHIVED"


async def test_seed_from_matrix_endpoint_writes_audit(hospital_id, monkeypatch):
    async def fake_seed(_db, _hospital_id):
        return {"created": 3, "skipped": 1, "backfilled": 0}

    monkeypatch.setattr(api, "seed_query_targets_from_matrix", fake_seed)
    db = _FakeDB(hospital=SimpleNamespace(id=hospital_id))

    result = await api.seed_query_targets_from_matrix_endpoint(hospital_id, db=db)

    assert result == {"created": 3, "skipped": 1, "backfilled": 0}
    log = _only_audit(db)
    assert log.action == "seed_query_targets_from_matrix"
    assert log.detail == {"created": 3, "skipped": 1, "backfilled": 0}


async def test_add_query_variant_writes_audit_with_variant_id(hospital_id):
    target = _target(hospital_id, platforms=["CHATGPT"])
    # 1) _get_target_or_404 → target, 2) _find_existing_variant → 중복 없음
    db = _FakeDB(results=[target, None])

    await api.add_query_variant(
        hospital_id,
        target.id,
        body=AIQueryVariantCreate(query_text="치질 수술 어디가 좋아", platform="GEMINI"),
        db=db,
    )

    log = _only_audit(db)
    assert log.action == "add_query_variant"
    assert log.target_type == "ai_query_variant"
    # flush 이후에 감사 행을 써야 대상 id가 확정된다.
    assert log.target_id is not None and log.target_id != "None"
    assert log.detail["platform"] == "GEMINI"


async def test_reactivating_existing_variant_writes_audit(hospital_id):
    target = _target(hospital_id)
    duplicate = _variant(target.id, is_active=False, platform="GEMINI")
    db = _FakeDB(results=[target, duplicate])

    await api.add_query_variant(
        hospital_id,
        target.id,
        body=AIQueryVariantCreate(query_text=duplicate.query_text, platform="GEMINI"),
        db=db,
    )

    log = _only_audit(db)
    assert log.action == "reactivate_query_variant"
    assert log.target_id == str(duplicate.id)


async def test_update_query_variant_audits_before_and_after_values(hospital_id):
    target = _target(hospital_id)
    variant = _variant(target.id, is_active=True)
    db = _FakeDB(results=[variant])

    await api.update_query_variant(
        hospital_id,
        target.id,
        variant.id,
        body=AIQueryVariantUpdate(is_active=False),
        db=db,
    )

    log = _only_audit(db)
    assert log.action == "update_query_variant"
    assert log.detail["changed_fields"]["is_active"] == {"from": True, "to": False}


async def test_deactivate_query_variant_writes_audit(hospital_id):
    target = _target(hospital_id)
    variant = _variant(target.id, platform="GEMINI")
    db = _FakeDB(results=[variant])

    await api.deactivate_query_variant(hospital_id, target.id, variant.id, db=db)

    log = _only_audit(db)
    assert log.action == "deactivate_query_variant"
    assert log.target_id == str(variant.id)
    assert log.detail["platform"] == "GEMINI"


def test_audit_value_coerces_non_json_types():
    """detail은 JSON 컬럼이다 — UUID 같은 값이 섞여 커밋이 깨지면 안 된다."""
    value = uuid.uuid4()
    assert api._audit_value(value) == str(value)
    assert api._audit_value([value, "x", None]) == [str(value), "x", None]
    assert api._audit_value(True) is True
