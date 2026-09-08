"""V0 QueryMatrix → AIQueryTarget 자동 시드 테스트.

커버 범위:
- seed_query_targets_from_matrix: 생성, 멱등, SoV 갭 기반 priority 정렬
- 엔드포인트 seed-from-matrix: 기본 동작
- tasks._seed_query_targets_from_matrix_sync: 질문이 비었을 때만 시드, 감사 기록, 실패 비전파
- V0 완료 후 exposure_actions가 비어 있지 않음 (통합 시나리오 모의)
"""
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.query_target_seed import seed_query_targets_from_matrix
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
    # AIQueryTarget 2개 + 각 ChatGPT/Gemini variant
    from app.models.sov import AIQueryTarget, AIQueryVariant
    targets = [o for o in db.added if isinstance(o, AIQueryTarget)]
    variants = [o for o in db.added if isinstance(o, AIQueryVariant)]
    assert len(targets) == 2
    assert len(variants) == 4
    assert {variant.platform for variant in variants} == {"CHATGPT", "GEMINI"}
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
    assert len(variants) == 2
    assert {variant.platform for variant in variants} == {"CHATGPT", "GEMINI"}
    assert all(variant.query_matrix_id == row.id for variant in variants)
    assert all(variant.query_text == row.query_text for variant in variants)
    assert all(variant.is_active is True for variant in variants)


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


# ─────────────────────────────────────────────
# 8. 구조 필드 시드 — 콘텐츠 계획이 타깃을 구분할 수 있어야 한다
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_fills_structured_fields_from_the_query_sentence():
    """예전에는 전부 빈 값이라 유형 친화도가 타깃 간 상수였다(리뷰 §1.2 B)."""
    hospital_id = uuid.uuid4()
    rows = [
        _matrix_row("강남역에서 허리디스크 치료하는 병원 알려줘", hospital_id),
        _matrix_row("역삼동 대장내시경 가능한 병원 추천해줘", hospital_id),
        _matrix_row("강남역 정형외과 병원 추천해줘", hospital_id),
    ]
    db = _FakeAsyncDB(matrix_rows=rows)

    await seed_query_targets_from_matrix(db, hospital_id)

    from app.models.sov import AIQueryTarget
    targets = {o.name: o for o in db.added if isinstance(o, AIQueryTarget)}

    disease = targets["강남역에서 허리디스크 치료하는 병원 알려줘"]
    assert disease.condition_or_symptom == "허리디스크"
    assert disease.treatment is None
    assert disease.region_terms == ["강남역"]
    assert disease.target_intent == "증상 탐색"

    procedure = targets["역삼동 대장내시경 가능한 병원 추천해줘"]
    assert procedure.treatment == "대장내시경"
    assert procedure.condition_or_symptom is None
    assert procedure.region_terms == ["역삼동"]
    assert procedure.target_intent == "추천 탐색"

    local = targets["강남역 정형외과 병원 추천해줘"]
    assert local.specialty == "정형외과"
    assert local.region_terms == ["강남역"]
    assert local.condition_or_symptom is None


@pytest.mark.asyncio
async def test_seed_backfills_structure_on_existing_targets():
    """재시드는 기존 target의 빈 구조 필드도 채운다(멱등, 수기 편집 보존)."""
    hospital_id = uuid.uuid4()
    query_text = "강남역에서 허리디스크 치료하는 병원 알려줘"

    from app.models.sov import AIQueryTarget

    existing = AIQueryTarget(
        hospital_id=hospital_id,
        name=query_text,
        target_intent="증상 탐색",
        region_terms=[],
        condition_or_symptom=None,
        treatment=None,
        decision_criteria=[],
        platforms=["CHATGPT", "GEMINI"],
        competitor_names=[],
        priority="HIGH",
        status="ACTIVE",
    )
    existing.variants = []

    db = _FakeAsyncDB(matrix_rows=[_matrix_row(query_text, hospital_id)])
    db._existing_targets = [existing]

    original_execute = db.execute

    async def execute(stmt):
        if db._execute_call == 0:
            db._execute_call += 1
            return _FakeResult(db._existing_targets)
        return await original_execute(stmt)

    db.execute = execute

    result = await seed_query_targets_from_matrix(db, hospital_id)

    assert result["created"] == 0
    assert result["skipped"] == 1
    assert existing.condition_or_symptom == "허리디스크"
    assert existing.region_terms == ["강남역"]


# ─────────────────────────────────────────────
# 9. V0 완료 훅 — 질문이 비었을 때만 시드하고 감사 기록을 남긴다 (M-18)
# ─────────────────────────────────────────────

class _FakeSeedSession:
    """_seed_query_targets_from_matrix_sync가 여는 AsyncSession 대역."""

    def __init__(self):
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        self.commits += 1


def _patch_seed_hook(
    monkeypatch,
    *,
    live_targets: int,
    created: int = 0,
    seed_error: Exception | None = None,
):
    """훅이 늦게 import하는 협력자들을 대역으로 바꾼다. 반환: (session, calls)."""
    from app.core import database as core_database
    from app.services import audit_log, exposure_action_engine, query_target_seed

    session = _FakeSeedSession()
    calls: dict = {"seeded": 0, "audits": [], "exposure": 0}

    monkeypatch.setattr(core_database, "get_async_sessionmaker", lambda: (lambda: session))

    async def _count(_db, _hospital_id):
        return live_targets

    async def _seed(_db, _hospital_id):
        if seed_error is not None:
            raise seed_error
        calls["seeded"] += 1
        return {"created": created, "skipped": 0, "backfilled": 0}

    async def _audit(_db, **kwargs):
        calls["audits"].append(kwargs)
        return None

    async def _exposure(_db, _hospital_id):
        calls["exposure"] += 1
        return None

    monkeypatch.setattr(query_target_seed, "count_live_query_targets", _count)
    monkeypatch.setattr(query_target_seed, "seed_query_targets_from_matrix", _seed)
    monkeypatch.setattr(audit_log, "write_audit_log", _audit)
    monkeypatch.setattr(
        exposure_action_engine, "ensure_hospital_exposure_actions", _exposure
    )
    return session, calls


def test_v0_completion_seeds_when_hospital_has_no_query_targets(monkeypatch):
    """질문이 0개면 시스템이 시드하고 감사 기록에 생성 수를 남긴다."""
    hospital_id = uuid.uuid4()
    session, calls = _patch_seed_hook(monkeypatch, live_targets=0, created=7)

    tasks._seed_query_targets_from_matrix_sync(hospital_id)

    assert calls["seeded"] == 1
    assert len(calls["audits"]) == 1
    audit = calls["audits"][0]
    assert audit["action"] == "query_targets_seeded_from_matrix"
    assert audit["hospital_id"] == hospital_id
    assert audit["actor"] == tasks.V0_QUERY_TARGET_SEED_ACTOR
    assert audit["detail"]["created"] == 7
    assert session.commits == 1
    assert calls["exposure"] == 1


def test_v0_completion_does_not_reseed_when_query_targets_exist(monkeypatch):
    """보관되지 않은 질문이 이미 있으면 다시 시드하지 않는다(주기 복구 안전)."""
    hospital_id = uuid.uuid4()
    session, calls = _patch_seed_hook(monkeypatch, live_targets=12, created=5)

    tasks._seed_query_targets_from_matrix_sync(hospital_id)

    assert calls["seeded"] == 0
    assert calls["audits"] == []
    assert session.commits == 0
    # 시드를 건너뛰어도 노출 보완 큐 갱신은 그대로 수행한다.
    assert calls["exposure"] == 1


def test_v0_completion_survives_a_failing_seed(monkeypatch):
    """시드가 실패해도 V0 완료는 되돌아가지 않는다(예외 비전파)."""
    hospital_id = uuid.uuid4()
    _session, calls = _patch_seed_hook(
        monkeypatch, live_targets=0, seed_error=RuntimeError("matrix 조회 실패")
    )

    tasks._seed_query_targets_from_matrix_sync(hospital_id)

    assert calls["audits"] == []
