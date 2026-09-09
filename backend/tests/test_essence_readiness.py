import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.essence import (
    AUTO_REVIEW_GAP_FIELD,
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
    get_essence_readiness_states,
    get_public_approved_philosophy_id,
    get_public_approved_philosophy_ids,
    get_public_essence_readiness,
    resolve_essence_readiness,
)
from app.services.evidence_noise import compute_evidence_noise_hash


def _source(*, status=SourceStatus.PROCESSED, source_type=SourceType.HOMEPAGE, hospital_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        content_hash="hash",
        status=status,
        source_type=source_type,
        processed_at=datetime.now(timezone.utc) if status == SourceStatus.PROCESSED else None,
    )


def test_current_base_reports_matching_approval_snapshot_as_fresh():
    source = _source()
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([source]))
    readiness = resolve_essence_readiness(philosophy, [source])
    assert readiness.current is philosophy
    assert readiness.public_philosophy is philosophy
    assert readiness.is_fresh is True


def test_new_pending_text_source_keeps_base_current_while_freshness_is_stale():
    processed = _source()
    pending = _source(status=SourceStatus.PENDING)
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([processed]))
    readiness = resolve_essence_readiness(philosophy, [processed, pending])
    assert readiness.current is philosophy
    assert readiness.public_philosophy is philosophy
    assert readiness.is_fresh is False
    assert readiness.is_stale is True
    assert readiness.has_unprocessed_sources is True


def test_changed_processed_snapshot_keeps_base_for_writes_and_public_reads():
    original = _source()
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([original]))
    changed = _source()

    readiness = resolve_essence_readiness(philosophy, [original, changed])

    assert readiness.current is philosophy
    assert readiness.public_philosophy is philosophy
    assert readiness.is_stale is True


def test_absorbed_new_processed_source_keeps_base_current():
    original = _source()
    new = _source()
    philosophy = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([original]),
        source_asset_ids=[original.id],
    )

    readiness = resolve_essence_readiness(philosophy, [original, new])

    assert readiness.current is philosophy
    assert readiness.public_philosophy is philosophy
    assert readiness.is_fresh is False
    assert readiness.is_stale is True


def _approved_row(
    approved_id,
    source_snapshot_hash,
    source_asset_ids,
    *,
    evidence_noise_hash=None,
    hospital_id=None,
    is_base=False,
    version=1,
):
    """SQLAlchemy Row처럼 이름으로 읽히는 승인 행 — 판정 함수가 컬럼 이름으로 읽는다."""
    return SimpleNamespace(
        id=approved_id,
        hospital_id=hospital_id,
        source_snapshot_hash=source_snapshot_hash,
        source_asset_ids=source_asset_ids,
        evidence_noise_hash=evidence_noise_hash,
        is_base=is_base,
        approved_at=None,
        version=version,
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


class _AsyncBatchReadinessDB:
    """묶음 조회 더블 — 병원 IN 조회 2회에 모든 승인·자료 행을 한 번에 돌려준다."""

    def __init__(self, approved_rows, source_rows):
        self._approved_rows = approved_rows
        self._source_rows = source_rows
        self.query_count = 0

    async def execute(self, statement):
        self.query_count += 1
        description = statement.column_descriptions[0]
        entity = description.get("entity")
        if entity is HospitalContentPhilosophy:
            return _AsyncResult(rows=self._approved_rows)
        if entity is HospitalSourceAsset:
            return _AsyncResult(rows=self._source_rows)
        raise AssertionError(f"예상하지 못한 묶음 readiness 조회: {description}")


@pytest.mark.asyncio
async def test_batched_public_ids_judge_each_hospital_like_the_single_lookup():
    """한 번의 묶음 조회가 병원마다 단건 조회와 같은 답을 낸다 — 조회 방식만 다르다."""
    fresh_hospital, stale_hospital = uuid.uuid4(), uuid.uuid4()
    fresh_id, stale_id = uuid.uuid4(), uuid.uuid4()
    fresh_source = _source(hospital_id=fresh_hospital)
    # 승인 이후 자료 본문이 바뀐 병원 — 공개 baseline이 깨졌으므로 공개 판정도 막힌다.
    original = _source(hospital_id=stale_hospital)
    changed = SimpleNamespace(**vars(original))
    changed.content_hash = "changed"
    fresh_row = _approved_row(
        fresh_id,
        compute_sources_snapshot_hash([fresh_source]),
        [fresh_source.id],
        hospital_id=fresh_hospital,
    )
    stale_row = _approved_row(
        stale_id,
        compute_sources_snapshot_hash([original]),
        [original.id],
        hospital_id=stale_hospital,
    )
    db = _AsyncBatchReadinessDB([fresh_row, stale_row], [fresh_source, changed])

    batched = await get_public_approved_philosophy_ids(db, [fresh_hospital, stale_hospital])

    assert batched == {fresh_hospital: fresh_id, stale_hospital: stale_id}
    assert db.query_count == 2
    assert (
        await get_public_approved_philosophy_id(
            _AsyncReadinessDB(fresh_row, [fresh_source]), fresh_hospital
        )
        == batched[fresh_hospital]
    )
    assert (
        await get_public_approved_philosophy_id(
            _AsyncReadinessDB(stale_row, [changed]), stale_hospital
        )
        == batched[stale_hospital]
    )


@pytest.mark.asyncio
async def test_batched_public_ids_read_nothing_for_an_empty_hospital_set():
    db = _AsyncBatchReadinessDB([], [])

    assert await get_public_approved_philosophy_ids(db, []) == {}
    assert db.query_count == 0


@pytest.mark.asyncio
async def test_batched_public_ids_prefer_flagged_base_over_rolling_approved_fallback():
    hospital_id = uuid.uuid4()
    base_id, rolling_id = uuid.uuid4(), uuid.uuid4()
    source = _source(hospital_id=hospital_id)
    snapshot = compute_sources_snapshot_hash([source])
    rolling = _approved_row(
        rolling_id, snapshot, [source.id], hospital_id=hospital_id, version=2
    )
    base = _approved_row(
        base_id,
        "approval-time-snapshot",
        [source.id],
        hospital_id=hospital_id,
        is_base=True,
        version=1,
    )
    # Deliberately reverse SQL ordering: Python selection must still protect the base.
    db = _AsyncBatchReadinessDB([rolling, base], [source])

    assert await get_public_approved_philosophy_ids(db, [hospital_id]) == {
        hospital_id: base_id
    }


@pytest.mark.asyncio
async def test_lightweight_ids_keep_base_during_pending_source_processing():
    approved_id = uuid.uuid4()
    original = _source()
    pending = _source(status=SourceStatus.PENDING)
    approved_row = _approved_row(approved_id, compute_sources_snapshot_hash([original]), [original.id])
    source_rows = [original, pending]
    db = _AsyncReadinessDB(approved_row, source_rows)

    assert await get_current_approved_philosophy_id(db, uuid.uuid4()) == approved_id
    assert await get_public_approved_philosophy_id(db, uuid.uuid4()) == approved_id


@pytest.mark.asyncio
async def test_lightweight_public_id_keeps_drifted_base():
    approved_id = uuid.uuid4()
    original = _source()
    approved_row = _approved_row(approved_id, compute_sources_snapshot_hash([original]), [original.id])
    changed = SimpleNamespace(**vars(original))
    changed.content_hash = "changed"
    db = _AsyncReadinessDB(approved_row, [changed])

    assert await get_public_approved_philosophy_id(db, uuid.uuid4()) == approved_id


def test_excluding_a_note_keeps_base_current_and_records_stale_metadata():
    source = _source()
    approved = SimpleNamespace(
        source_snapshot_hash=compute_sources_snapshot_hash([source]),
        evidence_noise_hash=compute_evidence_noise_hash([]),
        source_asset_ids=[str(source.id)],
    )

    readiness = resolve_essence_readiness(
        approved, [source], excluded_note_hash=compute_evidence_noise_hash([uuid.uuid4()])
    )

    assert readiness.current is approved
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
async def test_lightweight_id_keeps_base_after_changed_noise_set():
    approved_id = uuid.uuid4()
    original = _source()
    approved_row = _approved_row(
        approved_id,
        compute_sources_snapshot_hash([original]),
        [original.id],
        evidence_noise_hash=compute_evidence_noise_hash([]),
    )
    db = _AsyncReadinessDB(approved_row, [original], noise_rows=[uuid.uuid4()])

    assert await get_current_approved_philosophy_id(db, uuid.uuid4()) == approved_id
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


class _AsyncReadinessStatesDB:
    """3상태 묶음 조회 더블 — 승인·자료·노이즈·초안 4문장을 statement가 무엇을 고르는지로 가른다."""

    def __init__(self, approved_rows, source_rows, *, noise_rows=None, draft_rows=None):
        self._approved_rows = approved_rows
        self._source_rows = source_rows
        self._noise_rows = noise_rows or []
        self._draft_rows = draft_rows or []
        self.query_count = 0

    async def execute(self, statement):
        self.query_count += 1
        names = [description["name"] for description in statement.column_descriptions]
        entity = statement.column_descriptions[0].get("entity")
        if entity is HospitalContentPhilosophy:
            # 승인 행과 예외 초안은 같은 엔티티라 고르는 컬럼으로 가른다.
            return _AsyncResult(rows=self._draft_rows if "unsupported_gaps" in names else self._approved_rows)
        if entity is HospitalSourceAsset:
            return _AsyncResult(rows=self._source_rows)
        if entity is HospitalSourceEvidenceNote:
            return _AsyncResult(rows=self._noise_rows)
        raise AssertionError(f"예상하지 못한 3상태 묶음 조회: {names}")


def _draft_row(hospital_id, draft_id, snapshot_sources, *, reasons):
    """자동 검수가 보류한 DRAFT 행 — 자기가 만들어진 자료 판을 함께 들고 있다."""
    return SimpleNamespace(
        hospital_id=hospital_id,
        id=draft_id,
        source_snapshot_hash=compute_sources_snapshot_hash(snapshot_sources),
        unsupported_gaps=[
            {"field": AUTO_REVIEW_GAP_FIELD, "reason": reason} for reason in reasons
        ],
    )


def _states_case(*, sources, approved_sources, noise_hash=None):
    """한 병원의 묶음 행과 단건 더블을 같은 사실로 만든다 — 두 경로가 같은 답을 내야 한다."""
    hospital_id = uuid.uuid4()
    approved_row = _approved_row(
        uuid.uuid4(),
        compute_sources_snapshot_hash(approved_sources),
        [source.id for source in approved_sources],
        evidence_noise_hash=noise_hash if noise_hash is not None else compute_evidence_noise_hash([]),
        hospital_id=hospital_id,
    )
    single_db = _AsyncReadinessDB(
        SimpleNamespace(
            source_snapshot_hash=approved_row.source_snapshot_hash,
            source_asset_ids=approved_row.source_asset_ids,
            evidence_noise_hash=approved_row.evidence_noise_hash,
        ),
        sources,
    )
    return hospital_id, approved_row, single_db


@pytest.mark.asyncio
async def test_batched_states_judge_current_like_the_single_lookup():
    """신선·stale·미처리 — 묶음 판정이 단건 `get_essence_readiness`와 갈라지면 화면이 거짓말을 한다."""
    fresh_source = _source()
    fresh, fresh_row, fresh_single = _states_case(
        sources=[fresh_source], approved_sources=[fresh_source]
    )
    original = _source()
    changed = SimpleNamespace(**vars(original))
    changed.content_hash = "changed"
    stale, stale_row, stale_single = _states_case(
        sources=[changed], approved_sources=[original]
    )
    processed = _source()
    pending = _source(status=SourceStatus.PENDING)
    waiting, waiting_row, waiting_single = _states_case(
        sources=[processed, pending], approved_sources=[processed]
    )
    for hospital_id, rows in ((fresh, [fresh_source]), (stale, [changed]), (waiting, [processed, pending])):
        for row in rows:
            row.hospital_id = hospital_id
    db = _AsyncReadinessStatesDB(
        [fresh_row, stale_row, waiting_row],
        [fresh_source, changed, processed, pending],
    )

    states = await get_essence_readiness_states(db, [fresh, stale, waiting])

    assert db.query_count == 4
    assert states[fresh].current is True
    assert states[stale].current is True
    assert states[waiting].current is True
    assert states[waiting].unprocessed_sources == 1
    assert states[fresh].unprocessed_sources == 0
    assert all(state.escalated_draft is False for state in states.values())
    for hospital_id, single_db in ((fresh, fresh_single), (stale, stale_single), (waiting, waiting_single)):
        single = await get_essence_readiness(single_db, hospital_id)
        assert states[hospital_id].current is (single.current is not None)
        assert states[hospital_id].unprocessed_sources == (
            single.required_source_count - single.processed_source_count
        )


@pytest.mark.asyncio
async def test_batched_states_flag_a_hospital_with_an_escalated_draft():
    """자동 검수가 보류한 초안은 사람이 손대야 풀린다 — `current`가 살아 있어도 예외로 남는다."""
    source = _source()
    hospital_id, approved_row, single_db = _states_case(
        sources=[source], approved_sources=[source]
    )
    source.hospital_id = hospital_id
    draft_id = uuid.uuid4()
    db = _AsyncReadinessStatesDB(
        [approved_row],
        [source],
        draft_rows=[
            _draft_row(hospital_id, draft_id, [source], reasons=("근거 없는 효과 표현",))
        ],
    )

    states = await get_essence_readiness_states(db, [hospital_id])

    assert states[hospital_id].escalated_draft is True
    # 보류 사유는 현황 화면이 초안을 다시 조회하지 않도록 여기서 함께 실린다.
    assert states[hospital_id].escalated_draft_id == draft_id
    assert states[hospital_id].escalated_draft_findings == ("근거 없는 효과 표현",)
    assert states[hospital_id].current is True
    assert states[hospital_id].required_sources == 1
    assert (await get_essence_readiness(single_db, hospital_id)).current is not None


@pytest.mark.asyncio
async def test_an_escalated_draft_from_an_older_source_snapshot_is_not_an_exception():
    """자료가 바뀌어 새 판이 승인되면 옛 초안은 예외가 아니다 — 안 그러면 영영 "예외 있음"이다."""
    source = _source()
    hospital_id, approved_row, _single_db = _states_case(
        sources=[source], approved_sources=[source]
    )
    source.hospital_id = hospital_id
    stale_source = _source()
    db = _AsyncReadinessStatesDB(
        [approved_row],
        [source],
        # 초안이 선언한 자료 집합은 지금 집합이 아니다(쓰기 게이트의 `_drafts_for_snapshot`).
        draft_rows=[
            _draft_row(hospital_id, uuid.uuid4(), [stale_source], reasons=("근거 없는 효과 표현",))
        ],
    )

    states = await get_essence_readiness_states(db, [hospital_id])

    assert states[hospital_id].escalated_draft is False
    assert states[hospital_id].escalated_draft_id is None


@pytest.mark.asyncio
async def test_a_gap_without_a_reason_is_not_an_exception():
    """사유가 없으면 승인 게이트도 막지 않는다 — 목록의 예외 수와 현황의 카드가 갈리지 않게."""
    source = _source()
    hospital_id, approved_row, _single_db = _states_case(
        sources=[source], approved_sources=[source]
    )
    source.hospital_id = hospital_id
    db = _AsyncReadinessStatesDB(
        [approved_row],
        [source],
        draft_rows=[_draft_row(hospital_id, uuid.uuid4(), [source], reasons=("",))],
    )

    states = await get_essence_readiness_states(db, [hospital_id])

    assert states[hospital_id].escalated_draft is False
    assert states[hospital_id].escalated_draft_findings == ()


@pytest.mark.asyncio
async def test_batched_states_answer_for_a_hospital_without_an_approval():
    """승인 기준이 없는 병원도 키를 받는다 — 화면에서 빠지면 준비 중인 병원이 사라진다."""
    hospital_id = uuid.uuid4()
    pending = _source(status=SourceStatus.PENDING, hospital_id=hospital_id)
    db = _AsyncReadinessStatesDB([], [pending])

    states = await get_essence_readiness_states(db, [hospital_id])

    assert states[hospital_id].current is False
    assert states[hospital_id].unprocessed_sources == 1


@pytest.mark.asyncio
async def test_batched_states_read_nothing_for_an_empty_hospital_set():
    db = _AsyncReadinessStatesDB([], [])

    assert await get_essence_readiness_states(db, []) == {}
    assert db.query_count == 0
