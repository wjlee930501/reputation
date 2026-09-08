import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    SourceStatus,
    SourceType,
)
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.essence_readiness import (
    get_current_approved_philosophy_id,
    get_essence_readiness,
    get_public_approved_philosophy_id,
    get_public_essence_readiness,
    resolve_essence_readiness,
)
from app.services.evidence_noise import compute_evidence_noise_hash


def _source(*, status=SourceStatus.PROCESSED, source_type=SourceType.HOMEPAGE):
    return SimpleNamespace(
        id=uuid.uuid4(),
        content_hash="hash",
        status=status,
        source_type=source_type,
        processed_at=datetime.now(timezone.utc) if status == SourceStatus.PROCESSED else None,
    )


def test_current_approved_requires_exact_complete_source_snapshot():
    source = _source()
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([source]))
    readiness = resolve_essence_readiness(philosophy, [source])
    assert readiness.current is philosophy
    assert readiness.public_philosophy is philosophy
    assert readiness.is_fresh is True


def test_new_pending_text_source_immediately_makes_approval_stale():
    processed = _source()
    pending = _source(status=SourceStatus.PENDING)
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([processed]))
    readiness = resolve_essence_readiness(philosophy, [processed, pending])
    assert readiness.current is None
    assert readiness.public_philosophy is philosophy
    assert readiness.is_fresh is False
    assert readiness.is_stale is True
    assert readiness.has_unprocessed_sources is True


def test_changed_processed_snapshot_blocks_writes_and_public_reads():
    original = _source()
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([original]))
    changed = _source()

    readiness = resolve_essence_readiness(philosophy, [original, changed])

    assert readiness.current is None
    assert readiness.public_philosophy is None
    assert readiness.is_stale is True


def test_absorbed_new_processed_source_keeps_public_on_intact_approved_baseline():
    original = _source()
    new = _source()
    philosophy = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([original]),
        source_asset_ids=[original.id],
    )

    readiness = resolve_essence_readiness(philosophy, [original, new])

    assert readiness.current is None
    assert readiness.public_philosophy is philosophy
    assert readiness.is_fresh is False
    assert readiness.is_stale is True


def _approved_row(
    approved_id, source_snapshot_hash, source_asset_ids, *, evidence_noise_hash=None
):
    """SQLAlchemy Row처럼 이름으로 읽히는 승인 행 — 판정 함수가 컬럼 이름으로 읽는다."""
    return SimpleNamespace(
        id=approved_id,
        source_snapshot_hash=source_snapshot_hash,
        source_asset_ids=source_asset_ids,
        evidence_noise_hash=evidence_noise_hash,
    )


class _AsyncResult:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def one_or_none(self):
        return self._one

    def scalar_one_or_none(self):
        return self._one

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _AsyncReadinessDB:
    """readiness 조회 더블 — 호출 순서가 아니라 statement가 무엇을 고르는지로 응답한다.

    호출 순서로 응답하면 노이즈 조회를 건너뛰는 경로가 자료 행을 승인 행으로 받는 식으로
    조용히 어긋난다. `query_count`는 각 경로가 실제로 몇 번 조회하는지 확인하는 데 쓴다.
    """

    def __init__(self, approved_row, source_rows, *, noise_rows=None):
        self._approved_row = approved_row
        self._source_rows = source_rows
        self._noise_rows = noise_rows or []
        self.query_count = 0

    async def execute(self, statement):
        self.query_count += 1
        description = statement.column_descriptions[0]
        entity = description.get("entity")
        if entity is HospitalContentPhilosophy:
            return _AsyncResult(one=self._approved_row)
        if entity is HospitalSourceAsset:
            return _AsyncResult(rows=self._source_rows)
        if entity is HospitalSourceEvidenceNote or description.get("expr") is (
            HospitalSourceEvidenceNote.id
        ):
            return _AsyncResult(rows=self._noise_rows)
        raise AssertionError(f"예상하지 못한 readiness 조회: {description}")


@pytest.mark.asyncio
async def test_lightweight_ids_split_strict_write_from_intact_public_baseline():
    approved_id = uuid.uuid4()
    original = _source()
    pending = _source(status=SourceStatus.PENDING)
    approved_row = _approved_row(approved_id, compute_sources_snapshot_hash([original]), [original.id])
    source_rows = [original, pending]
    db = _AsyncReadinessDB(approved_row, source_rows)

    assert await get_current_approved_philosophy_id(db, uuid.uuid4()) is None
    assert await get_public_approved_philosophy_id(db, uuid.uuid4()) == approved_id


@pytest.mark.asyncio
async def test_lightweight_public_id_rejects_changed_approved_baseline():
    approved_id = uuid.uuid4()
    original = _source()
    approved_row = _approved_row(approved_id, compute_sources_snapshot_hash([original]), [original.id])
    changed = SimpleNamespace(**vars(original))
    changed.content_hash = "changed"
    db = _AsyncReadinessDB(approved_row, [changed])

    assert await get_public_approved_philosophy_id(db, uuid.uuid4()) is None


def test_excluding_a_note_makes_strict_current_stale_but_keeps_public_baseline():
    """H-02: 운영자가 뺀 주장으로 새 글을 만들면 안 되지만, 기존 공개 글의 근거는 그대로다."""
    source = _source()
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=compute_evidence_noise_hash([]),
        source_asset_ids=[str(source.id)],
    )

    readiness = resolve_essence_readiness(
        approved, [source], excluded_note_hash=compute_evidence_noise_hash([uuid.uuid4()])
    )

    assert readiness.current is None
    assert readiness.public_philosophy is approved
    assert readiness.is_stale is True


def test_matching_noise_hash_keeps_current():
    source = _source()
    noise_hash = compute_evidence_noise_hash([uuid.uuid4()])
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=noise_hash,
        source_asset_ids=[str(source.id)],
    )

    readiness = resolve_essence_readiness(approved, [source], excluded_note_hash=noise_hash)

    assert readiness.current is approved


def test_legacy_approval_without_noise_hash_is_not_stale_by_noise():
    """컬럼 이전 승인(NULL)은 노이즈 상태로 stale이 되지 않는다 — Task 3의 자동 갱신이 값을 쓴다."""
    source = _source()
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=None,
        source_asset_ids=[str(source.id)],
    )

    readiness = resolve_essence_readiness(
        approved, [source], excluded_note_hash=compute_evidence_noise_hash([uuid.uuid4()])
    )

    assert readiness.current is approved


@pytest.mark.asyncio
async def test_lightweight_strict_id_rejects_a_changed_noise_set():
    """근거에서 뺀 노트 집합이 승인 이후 달라지면 생성 게이트만 닫히고 공개 baseline은 남는다."""
    approved_id = uuid.uuid4()
    original = _source()
    approved_row = _approved_row(
        approved_id,
        compute_sources_snapshot_hash([original]),
        [original.id],
        evidence_noise_hash=compute_evidence_noise_hash([]),
    )
    db = _AsyncReadinessDB(approved_row, [original], noise_rows=[uuid.uuid4()])

    assert await get_current_approved_philosophy_id(db, uuid.uuid4()) is None
    assert await get_public_approved_philosophy_id(db, uuid.uuid4()) == approved_id


@pytest.mark.asyncio
async def test_public_readiness_skips_the_noise_query_the_strict_one_pays():
    """공개 읽기는 `public_philosophy`만 보므로 노이즈 집합 조회를 하지 않는다."""
    source = _source()
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=compute_evidence_noise_hash([]),
        source_asset_ids=[str(source.id)],
    )
    public_db = _AsyncReadinessDB(approved, [source])
    strict_db = _AsyncReadinessDB(approved, [source])

    public = await get_public_essence_readiness(public_db, uuid.uuid4())
    strict = await get_essence_readiness(strict_db, uuid.uuid4())

    assert public is approved
    assert public_db.query_count == 2
    assert strict.current is approved
    assert strict_db.query_count == 3
