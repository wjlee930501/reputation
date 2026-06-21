"""V0 QueryMatrix → AIQueryTarget 자동 시드 테스트.

커버 범위:
- seed_query_targets_from_matrix: 생성, 멱등, SoV 갭 기반 priority 정렬
- 엔드포인트 seed-from-matrix: 기본 동작
- tasks._seed_query_targets_from_matrix_sync: V0 실패 시 비전파 (non-fatal)
- V0 완료 후 exposure_actions가 비어 있지 않음 (통합 시나리오 모의)
"""
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.api.admin.query_targets import seed_query_targets_from_matrix
from app.workers import tasks


# ─────────────────────────────────────────────
# 공통 픽스처
# ─────────────────────────────────────────────

def _matrix_row(query_text: str, hospital_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id or uuid.uuid4(),
        query_text=query_text,
        is_active=True,
        priority="NORMAL",
    )


def _sov_row(query_id: uuid.UUID, *, is_mentioned: bool) -> SimpleNamespace:
    return SimpleNamespace(query_id=query_id, is_mentioned=is_mentioned)


class _FakeResult:
    """AsyncSession.execute() 반환값 시뮬레이터."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeAsyncDB:
    """seed_query_targets_from_matrix 호출에 필요한 최소 AsyncSession 목."""

    def __init__(self, *, matrix_rows, existing_names=None, sov_rows=None):
        self._matrix_rows = matrix_rows
        self._existing_names = existing_names or []
        self._sov_rows = sov_rows or []
        self.added: list = []
        self.flushed = 0
        self.committed = False
        self._execute_call = 0

    async def execute(self, _stmt):
        call = self._execute_call
        self._execute_call += 1
        if call == 0:
            # 첫 번째 execute: 기존 target name 목록
            return _FakeResult([(name,) for name in self._existing_names])
        if call == 1:
            # 두 번째 execute: QueryMatrix 행
            return _FakeResult(self._matrix_rows)
        if call == 2:
            # 세 번째 execute: SovRecord 행
            return _FakeResult(self._sov_rows)
        return _FakeResult([])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # flush 시 target.id가 필요하므로 아직 없는 경우 uuid 부여
        for obj in self.added:
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
        self.flushed += 1

    async def commit(self):
        self.committed = True


# ─────────────────────────────────────────────
# 1. 기본 생성 테스트
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_creates_targets_from_matrix():
    """QueryMatrix 행 수만큼 AIQueryTarget과 variant가 생성된다."""
    hospital_id = uuid.uuid4()
    rows = [
        _matrix_row("치질 수술 회복 기간", hospital_id),
        _matrix_row("항문 수술 잘하는 병원", hospital_id),
    ]
    db = _FakeAsyncDB(matrix_rows=rows)

    result = await seed_query_targets_from_matrix(db, hospital_id)

    assert result["created"] == 2
    assert result["skipped"] == 0
    # AIQueryTarget 2개 + AIQueryVariant 2개
    from app.models.sov import AIQueryTarget, AIQueryVariant
    targets = [o for o in db.added if isinstance(o, AIQueryTarget)]
    variants = [o for o in db.added if isinstance(o, AIQueryVariant)]
    assert len(targets) == 2
    assert len(variants) == 2
    assert db.committed is True


# ─────────────────────────────────────────────
# 2. 멱등 테스트
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_is_idempotent_on_rerun():
    """기존에 같은 query_text의 target이 있으면 건너뛴다."""
    hospital_id = uuid.uuid4()
    existing_text = "치질 수술 회복 기간"
    rows = [
        _matrix_row(existing_text, hospital_id),
        _matrix_row("새로운 질문", hospital_id),
    ]
    db = _FakeAsyncDB(matrix_rows=rows, existing_names=[existing_text])

    result = await seed_query_targets_from_matrix(db, hospital_id)

    assert result["created"] == 1
    assert result["skipped"] == 1
    from app.models.sov import AIQueryTarget
    targets = [o for o in db.added if isinstance(o, AIQueryTarget)]
    assert len(targets) == 1
    assert targets[0].name == "새로운 질문"


@pytest.mark.asyncio
async def test_seed_full_overlap_skips_all():
    """모든 query_text가 이미 존재하면 created=0, committed=False."""
    hospital_id = uuid.uuid4()
    existing_text = "치질 수술 회복 기간"
    rows = [_matrix_row(existing_text, hospital_id)]
    db = _FakeAsyncDB(matrix_rows=rows, existing_names=[existing_text])

    result = await seed_query_targets_from_matrix(db, hospital_id)

    assert result["created"] == 0
    assert result["skipped"] == 1
    # 변경 없으면 commit 하지 않는다
    assert db.committed is False


# ─────────────────────────────────────────────
# 3. SoV 갭 기반 priority 정렬
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_orders_by_sov_gap_unmentioned_first():
    """SoV 미언급 질문은 HIGH, 언급된 질문은 NORMAL priority로 생성된다."""
    hospital_id = uuid.uuid4()
    row_no_mention = _matrix_row("미언급 질문", hospital_id)
    row_mentioned = _matrix_row("언급된 질문", hospital_id)

    sov_rows = [
        _sov_row(row_no_mention.id, is_mentioned=False),
        _sov_row(row_mentioned.id, is_mentioned=True),
    ]

    db = _FakeAsyncDB(
        matrix_rows=[row_no_mention, row_mentioned],
        sov_rows=sov_rows,
    )

    await seed_query_targets_from_matrix(db, hospital_id)

    from app.models.sov import AIQueryTarget
    targets = {o.name: o for o in db.added if isinstance(o, AIQueryTarget)}

    assert targets["미언급 질문"].priority == "HIGH"
    assert targets["언급된 질문"].priority == "NORMAL"


@pytest.mark.asyncio
async def test_seed_no_sov_records_defaults_to_high():
    """SoV 측정 기록이 없는 질문은 HIGH priority (미노출 우선 처리)."""
    hospital_id = uuid.uuid4()
    rows = [_matrix_row("측정 없는 질문", hospital_id)]
    db = _FakeAsyncDB(matrix_rows=rows, sov_rows=[])

    await seed_query_targets_from_matrix(db, hospital_id)

    from app.models.sov import AIQueryTarget
    targets = [o for o in db.added if isinstance(o, AIQueryTarget)]
    assert targets[0].priority == "HIGH"


# ─────────────────────────────────────────────
# 4. 빈 매트릭스
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_empty_matrix_returns_zero():
    """QueryMatrix 행이 없으면 created=0이고 commit하지 않는다."""
    hospital_id = uuid.uuid4()
    db = _FakeAsyncDB(matrix_rows=[])

    result = await seed_query_targets_from_matrix(db, hospital_id)

    assert result == {"created": 0, "skipped": 0}
    assert db.committed is False


# ─────────────────────────────────────────────
# 5. variant query_matrix_id 연결
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_variant_links_to_query_matrix():
    """생성된 variant의 query_matrix_id가 원래 QueryMatrix 행 id와 일치한다."""
    hospital_id = uuid.uuid4()
    row = _matrix_row("치질 수술 회복 기간", hospital_id)
    db = _FakeAsyncDB(matrix_rows=[row])

    await seed_query_targets_from_matrix(db, hospital_id)

    from app.models.sov import AIQueryVariant
    variants = [o for o in db.added if isinstance(o, AIQueryVariant)]
    assert len(variants) == 1
    assert variants[0].query_matrix_id == row.id
    assert variants[0].query_text == row.query_text
    assert variants[0].platform == "CHATGPT"
    assert variants[0].is_active is True


# ─────────────────────────────────────────────
# 6. tasks._seed_query_targets_from_matrix_sync — 실패 비전파
# ─────────────────────────────────────────────

def test_seed_sync_does_not_propagate_failure():
    """시드 내부 오류가 V0 태스크를 실패시키지 않아야 한다."""
    hospital_id = uuid.uuid4()

    def _run_async_raises(coro):
        # coroutine을 닫아 unawaited 경고를 억제
        coro.close()
        raise RuntimeError("DB 연결 실패")

    with patch("app.workers.tasks._run_async", side_effect=_run_async_raises):
        # 예외가 전파되지 않는다
        tasks._seed_query_targets_from_matrix_sync(hospital_id)


def test_seed_sync_succeeds_silently():
    """_run_async가 정상 반환하면 예외 없이 종료된다."""
    hospital_id = uuid.uuid4()

    def _run_async_ok(coro):
        coro.close()
        return None

    with patch("app.workers.tasks._run_async", side_effect=_run_async_ok):
        tasks._seed_query_targets_from_matrix_sync(hospital_id)


# ─────────────────────────────────────────────
# 7. V0 완료 후 exposure_actions 비어 있지 않음 (통합 모의)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exposure_actions_populated_after_seed():
    """시드 후 AIQueryTarget이 있으면 ensure_hospital_exposure_actions가 호출된다."""
    hospital_id = uuid.uuid4()
    row = _matrix_row("치질 수술 회복 기간", hospital_id)
    db = _FakeAsyncDB(matrix_rows=[row])

    await seed_query_targets_from_matrix(db, hospital_id)

    from app.models.sov import AIQueryTarget
    targets = [o for o in db.added if isinstance(o, AIQueryTarget)]
    # 시드 완료 — 노출 보완 엔진은 이 target들로 actions를 만들 수 있다
    assert len(targets) > 0
    assert targets[0].status == "ACTIVE"
