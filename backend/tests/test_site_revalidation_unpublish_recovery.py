"""내림(unpublish) 캐시 무효화도 내구성 있는 재시도를 얻는다.

반려된 글은 status가 PUBLISHED가 아니고 반려 경로가 published_at까지 지우므로,
예전 조회 조건(status == PUBLISHED AND published_at IS NOT NULL)에서는 첫 revalidate
실패 뒤 재시도가 통째로 사라졌다 — 의료광고 위반 글이 ISR 캐시에 최대 1800~3600초
계속 서빙되는 상태. 여기서는 복구 계획 조회와 재시도 컨텍스트가 같은 완화 조건을
쓰는지, 그리고 올림/내림 run이 idempotency key에서 충돌하지 않는지를 고정한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.content import ContentItem, ContentStatus
from app.models.operations import OperationRun, OperationRunState
from app.services import site_revalidation_control as control
from app.workers import tasks

_PUBLISHED_AT = datetime(2026, 8, 20, 23, 5, tzinfo=UTC)


class _FakeAsyncSession:
    """start_revalidation_failure가 쓰는 좁은 표면만 흉내낸다."""

    def __init__(self, hospital, content, existing_runs=None):
        self._hospital = hospital
        self._content = content
        # 멱등 키 → 이미 열려 있는(또는 닫힌) run. 키가 겹치는지를 그대로 재현한다.
        self._existing_runs = existing_runs or {}
        self.added: list[OperationRun] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, _statement):
        row = None if self._content is None else (self._hospital, self._content)
        return SimpleNamespace(one_or_none=lambda: row)

    async def scalar(self, statement):
        """조회한 멱등 키에 해당하는 run만 돌려준다 (없으면 새로 연다)."""
        if not self._existing_runs:
            return None
        for value in statement.compile().params.values():
            if isinstance(value, str) and value in self._existing_runs:
                return self._existing_runs[value]
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1


def _hospital(slug: str = "test-clinic"):
    return SimpleNamespace(id=uuid.uuid4(), slug=slug, name="테스트의원")


def _content(hospital_id, *, status, published_at, content_id=None):
    return ContentItem(
        id=content_id or uuid.uuid4(),
        hospital_id=hospital_id,
        status=status,
        published_at=published_at,
    )


def _install(monkeypatch, hospital, content, existing_runs=None) -> _FakeAsyncSession:
    session = _FakeAsyncSession(hospital, content, existing_runs)
    monkeypatch.setattr(control, "get_async_sessionmaker", lambda: (lambda: session))

    async def _no_incident(_db, _run, *, terminal):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(control, "_touch_incident", _no_incident)
    return session


# ── (a) 반려된 글도 복구 계획과 재시도 컨텍스트를 얻는다 ───────────────────


@pytest.mark.asyncio
async def test_rejected_item_still_opens_durable_revalidation_recovery(monkeypatch):
    hospital = _hospital()
    content = _content(hospital.id, status=ContentStatus.REJECTED, published_at=None)
    session = _install(monkeypatch, hospital, content)

    plan = await control.start_revalidation_failure(
        hospital.slug, content.id, unpublished_from=_PUBLISHED_AT
    )

    assert plan is not None and plan.created is True and plan.delay_seconds == 60
    (run,) = session.added
    assert run.request_payload == {
        "content_id": str(content.id),
        "direction": control.DIRECTION_UNPUBLISH,
    }
    assert run.idempotency_key == (
        f"site-revalidation:{content.id}:unpublish:{_PUBLISHED_AT.isoformat()}"
    )


def test_retry_context_covers_unpublished_item_with_full_publish_path_set(monkeypatch):
    """재시도 컨텍스트도 같은 완화 조건 — 그리고 경로 집합은 올림과 동일하다."""
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        slug="test-clinic",
        treatments=[{"name": "도수치료"}],
    )
    content = _content(hospital.id, status=ContentStatus.REJECTED, published_at=None)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        state="RUNNING",
        attempt_count=0,
        hospital_id=hospital.id,
        request_payload={
            "content_id": str(content.id),
            "direction": control.DIRECTION_UNPUBLISH,
        },
    )
    _install_sync_session(monkeypatch, run, hospital, content)

    paths = tasks._site_revalidation_context(run.id, 0)

    assert paths == tasks.content_site_paths(
        hospital.slug, content.id, hospital.treatments
    )
    assert f"/{hospital.slug}/contents/{content.id}" in paths
    assert "/test-clinic/llms.txt" in paths and "/llms.txt" in paths


# ── (b) 발행된 적 없는 아이템은 그대로 건너뛴다 ────────────────────────────


@pytest.mark.asyncio
async def test_never_published_item_opens_no_recovery_run(monkeypatch):
    hospital = _hospital()
    content = _content(hospital.id, status=ContentStatus.DRAFT, published_at=None)
    session = _install(monkeypatch, hospital, content)

    plan = await control.start_revalidation_failure(hospital.slug, content.id)

    assert plan is None
    assert session.added == [] and session.commits == 0


def test_retry_context_skips_never_published_item(monkeypatch):
    hospital = SimpleNamespace(id=uuid.uuid4(), slug="test-clinic", treatments=[])
    content = _content(hospital.id, status=ContentStatus.DRAFT, published_at=None)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        state="RUNNING",
        attempt_count=0,
        hospital_id=hospital.id,
        request_payload={"content_id": str(content.id)},
    )
    _install_sync_session(monkeypatch, run, hospital, content)

    assert tasks._site_revalidation_context(run.id, 0) is None


# ── (c) 올림/내림 run은 같은 아이템이라도 키가 겹치지 않는다 ───────────────


@pytest.mark.asyncio
async def test_publish_and_unpublish_runs_do_not_share_idempotency_key(monkeypatch):
    hospital = _hospital()
    content_id = uuid.uuid4()

    published = _content(
        hospital.id,
        status=ContentStatus.PUBLISHED,
        published_at=_PUBLISHED_AT,
        content_id=content_id,
    )
    publish_session = _install(monkeypatch, hospital, published)
    publish_plan = await control.start_revalidation_failure(hospital.slug, content_id)

    rejected = _content(
        hospital.id,
        status=ContentStatus.REJECTED,
        published_at=None,
        content_id=content_id,
    )
    unpublish_session = _install(monkeypatch, hospital, rejected)
    unpublish_plan = await control.start_revalidation_failure(
        hospital.slug, content_id, unpublished_from=_PUBLISHED_AT
    )

    assert publish_plan is not None and unpublish_plan is not None
    (publish_run,) = publish_session.added
    (unpublish_run,) = unpublish_session.added
    assert publish_run.idempotency_key != unpublish_run.idempotency_key
    # 올림 키는 기존 형식을 그대로 유지한다 (진행 중이던 run과의 호환).
    assert publish_run.idempotency_key == (
        f"site-revalidation:{content_id}:{_PUBLISHED_AT.isoformat()}"
    )
    assert publish_run.request_payload["direction"] == control.DIRECTION_PUBLISH
    assert unpublish_run.request_payload["direction"] == control.DIRECTION_UNPUBLISH


# ── (d) 발행 후 본문 수정판의 갱신 실패도 재시도를 얻는다 ─────────────────


_EDITED_AT = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)


def _succeeded_run(hospital_id, content_id, key) -> OperationRun:
    """직전 판의 캐시 갱신이 성공으로 닫힌 상태."""
    return OperationRun(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="SITE_REVALIDATION",
        state=OperationRunState.SUCCEEDED.value,
        idempotency_key=key,
        request_payload={"content_id": str(content_id), "direction": control.DIRECTION_PUBLISH},
        attempt_count=1,
        total_count=1,
    )


@pytest.mark.asyncio
async def test_edit_after_a_successful_refresh_opens_a_new_retry(monkeypatch):
    """발행 성공 → AE 본문 수정 → 갱신 실패면 새 run이 열려야 한다.

    published_at만으로 판을 식별하면 수정본의 실패가 이미 SUCCEEDED로 닫힌 run에
    흡수돼 재시도도 인시던트도 없이 사라진다 (공개 표면에 옛 본문 잔존).
    """
    hospital = _hospital()
    content_id = uuid.uuid4()
    first_key = f"site-revalidation:{content_id}:{_PUBLISHED_AT.isoformat()}"

    edited = _content(
        hospital.id,
        status=ContentStatus.PUBLISHED,
        published_at=_PUBLISHED_AT,
        content_id=content_id,
    )
    edited.body_updated_at = _EDITED_AT
    session = _install(
        monkeypatch,
        hospital,
        edited,
        {first_key: _succeeded_run(hospital.id, content_id, first_key)},
    )

    plan = await control.start_revalidation_failure(hospital.slug, content_id)

    assert plan is not None
    assert plan.created is True
    assert plan.delay_seconds == 60
    (run,) = session.added
    assert run.idempotency_key == f"{first_key}:r{_EDITED_AT.isoformat()}"
    assert run.request_payload["direction"] == control.DIRECTION_PUBLISH


@pytest.mark.asyncio
async def test_same_edition_failure_still_reuses_the_closed_run(monkeypatch):
    """같은 판(수정 없음)의 반복 실패는 예전처럼 기존 run으로 합쳐진다 — 키는 판 단위다."""
    hospital = _hospital()
    content_id = uuid.uuid4()
    first_key = f"site-revalidation:{content_id}:{_PUBLISHED_AT.isoformat()}"
    published = _content(
        hospital.id,
        status=ContentStatus.PUBLISHED,
        published_at=_PUBLISHED_AT,
        content_id=content_id,
    )
    session = _install(
        monkeypatch,
        hospital,
        published,
        {first_key: _succeeded_run(hospital.id, content_id, first_key)},
    )

    plan = await control.start_revalidation_failure(hospital.slug, content_id)

    assert plan is not None
    assert plan.created is False
    assert plan.delay_seconds is None
    assert session.added == []


def test_run_direction_defaults_to_publish_for_legacy_payloads():
    assert control.run_revalidation_direction({}) == control.DIRECTION_PUBLISH
    assert control.run_revalidation_direction(None) == control.DIRECTION_PUBLISH
    assert (
        control.run_revalidation_direction({"direction": "UNPUBLISH"})
        == control.DIRECTION_UNPUBLISH
    )


# ── 헬퍼 ───────────────────────────────────────────────────────────────────


def _install_sync_session(monkeypatch, run, hospital, content) -> None:
    class _SyncSession:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, model, object_id):
            if object_id == run.id:
                return run
            if object_id == hospital.id:
                return hospital
            if content is not None and object_id == content.id:
                return content
            return None

    monkeypatch.setattr(tasks, "SyncSessionLocal", _SyncSession)
