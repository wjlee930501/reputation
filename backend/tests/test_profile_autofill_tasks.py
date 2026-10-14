"""계약 등록 직후 자동 입력 초안 작업."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.models.hospital import HospitalStatus
from app.services.hospital_profile_autofill import AutofillResult, SourceStatus
from app.workers import profile_autofill_tasks as module
from app.workers.dispatch_envelope import expected_purpose, expected_target


class _FakeSession:
    def __init__(self, hospital, existing=None):
        self.hospital = hospital
        self.existing = existing
        self.added: list = []
        self.executed: list = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, _model, _id):
        return self.hospital

    async def scalar(self, _stmt):
        return self.existing

    def add(self, row):
        self.added.append(row)

    async def execute(self, stmt):
        self.executed.append(stmt)
        return SimpleNamespace(rowcount=1)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True

    async def rollback(self):
        return None


def _patch(monkeypatch, session, *, allowed=True, result=None, apply_error=None):
    calls: list = []
    applied: list = []

    async def fake_autofill(name, *, website_url=None, blog_url=None, hospital_id=None, **kwargs):
        calls.append((name, website_url, blog_url, hospital_id, kwargs))
        return result or AutofillResult(
            draft={"address": "서울시 강남구"},
            field_meta={"address": {"source": "naver", "confidence": 0.9, "verified": True}},
            sources=[SourceStatus("네이버 플레이스", ok=True)],
            naver_identity="VERIFIED_SEARCH",
        )

    async def fake_guard(category):
        assert category == "content"
        return SimpleNamespace(allowed=allowed, reason=None if allowed else "limit")

    async def fake_survey(_db, _hospital_id):
        return "원장 설문 본문"

    async def fake_apply(_db, hospital_id, summary, **kwargs):
        if apply_error is not None:
            raise apply_error
        applied.append((hospital_id, summary, kwargs))
        return SimpleNamespace(status="APPLIED")

    monkeypatch.setattr(module, "get_async_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(module, "autofill_profile", fake_autofill)
    monkeypatch.setattr(module, "load_survey_text", fake_survey)
    monkeypatch.setattr(module, "apply_autofill_draft", fake_apply)
    monkeypatch.setattr(module.cost_guard, "check_and_increment", fake_guard)
    return SimpleNamespace(calls=calls, applied=applied)


async def test_stores_the_draft_then_applies_it(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="자동입력의원", website_url=None,
                               blog_url=None, address=None, naver_place_id=None,
                               naver_place_url=None, region=["강남구"], status="ONBOARDING")
    session = _FakeSession(hospital)
    spy = _patch(monkeypatch, session)

    outcome = await module._autofill_once(hospital.id)

    assert outcome == {"status": "DRAFT_STORED", "apply_status": "APPLIED"}
    [(name, website, blog, hospital_id, extra)] = spy.calls
    assert (name, website, blog, hospital_id) == ("자동입력의원", None, None, hospital.id)
    assert extra["regions"] == ["강남구"] and extra["survey_text"] == "원장 설문 본문"
    run = next(r for r in session.added if getattr(r, "operation_type", None) == "PROFILE_AUTOFILL")
    # 유료 호출 전에 RUNNING으로 선점했고, 수집 결과는 그 선점의 소유자로만 저장했다.
    assert run.idempotency_key == "registration" and run.state == "RUNNING"
    assert run.lease_owner and run.lease_expires_at is not None
    [store] = [stmt for stmt in session.executed if stmt.__visit_name__ == "update"]
    values = {col.key: bind.value for col, bind in store._values.items()}
    assert values["state"] == "SUCCEEDED" and values["lease_owner"] is None
    assert values["result_summary"]["draft"] == {"address": "서울시 강남구"}
    assert values["result_summary"]["naver_identity"] == "VERIFIED_SEARCH"
    assert values["result_summary"]["collected_without_channels"] is True
    assert values["result_summary"]["apply_status"] == "PENDING"
    audit = next(r for r in session.added if getattr(r, "action", None) == "autofill_profile")
    assert audit.actor == "system:profile-autofill"
    [(applied_hospital, summary, kwargs)] = spy.applied
    assert applied_hospital == hospital.id and summary["draft"] == {"address": "서울시 강남구"}
    assert kwargs["allow_live"] is False and kwargs["collected_without_channels"] is True
    assert session.committed is True


async def test_paused_hospital_is_not_collected(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="x", status=HospitalStatus.PAUSED)
    session = _FakeSession(hospital)
    spy = _patch(monkeypatch, session)

    assert (await module._autofill_once(hospital.id))["status"] == "SKIPPED_PAUSED"
    assert spy.calls == []


async def test_a_failed_apply_keeps_the_draft_for_a_cost_free_reapply(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="x", website_url="https://a.example",
                               blog_url=None, naver_place_id=None, naver_place_url=None,
                               region=[], status="ONBOARDING")
    session = _FakeSession(hospital)
    _patch(monkeypatch, session, apply_error=RuntimeError("boom"))
    marked = []

    async def fake_mark(run_id, error):
        marked.append(error)

    monkeypatch.setattr(module, "_mark_apply_failed", fake_mark)

    outcome = await module._autofill_once(hospital.id, "backfill")

    assert outcome == {"status": "DRAFT_STORED", "apply_status": "FAILED"}
    assert marked == ["RuntimeError"]


async def test_existing_draft_is_not_bought_again(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="x", website_url=None, blog_url=None)
    session = _FakeSession(
        hospital, existing=SimpleNamespace(id=uuid.uuid4(), state="SUCCEEDED")
    )
    spy = _patch(monkeypatch, session)

    assert (await module._autofill_once(hospital.id))["status"] == "ALREADY_DONE"
    assert spy.calls == []


async def test_a_live_claim_by_another_task_is_not_bought_again(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="x", website_url=None, blog_url=None)
    running = SimpleNamespace(
        id=uuid.uuid4(),
        state="RUNNING",
        lease_owner="other",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    session = _FakeSession(hospital, existing=running)
    spy = _patch(monkeypatch, session)

    assert (await module._autofill_once(hospital.id))["status"] == "ALREADY_RUNNING"
    assert spy.calls == [] and running.lease_owner == "other"


async def test_cost_guard_blocks_before_the_provider_and_releases_the_claim(monkeypatch) -> None:
    hospital = SimpleNamespace(id=uuid.uuid4(), name="x", website_url=None, blog_url=None)
    session = _FakeSession(hospital)
    spy = _patch(monkeypatch, session, allowed=False)

    assert (await module._autofill_once(hospital.id))["status"] == "COST_BLOCKED"
    assert spy.calls == []
    # 선점을 지워 다음 틱이 다시 집게 한다 — 실행 키를 태우지 않는다.
    assert [stmt.__visit_name__ for stmt in session.executed] == ["delete"]


def test_dispatch_is_purpose_and_hospital_bound() -> None:
    name = "app.workers.profile_autofill_tasks.autofill_registered_hospital_profile"
    hospital_id = str(uuid.uuid4())
    assert expected_purpose(name) == "autofill-registered-profile"
    assert expected_target(name, [hospital_id]) == hospital_id


# ── 회수 스윕의 실행 키 결정 ───────────────────────────────────────────
_NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)


def _run(key, *, days_ago=0.0, **summary):
    return SimpleNamespace(
        id=uuid.uuid4(),
        idempotency_key=key,
        result_summary=summary,
        completed_at=_NOW - timedelta(days=days_ago),
        created_at=_NOW - timedelta(days=days_ago),
    )


_GOOD = {"draft": {"address": "서울"}, "sources": [{"ok": True}], "apply_status": "APPLIED"}
_EMPTY = {"draft": {}, "sources": [{"ok": False}], "apply_status": "NOTHING_TO_APPLY"}


def test_a_hospital_without_runs_is_backfilled() -> None:
    assert module.next_autofill_key([], now=_NOW) == "backfill"


def test_a_good_run_needs_nothing() -> None:
    assert module.next_autofill_key([_run("registration", **_GOOD)], now=_NOW) is None


def test_empty_drafts_retry_after_1_3_7_days_then_stop() -> None:
    assert module.next_autofill_key([_run("registration", days_ago=0.5, **_EMPTY)], now=_NOW) is None
    assert module.next_autofill_key([_run("registration", days_ago=1, **_EMPTY)], now=_NOW) == "retry:1"
    runs = [_run("registration", days_ago=10, **_EMPTY), _run("retry:1", days_ago=2, **_EMPTY)]
    assert module.next_autofill_key(runs, now=_NOW) is None
    runs[-1] = _run("retry:1", days_ago=3, **_EMPTY)
    assert module.next_autofill_key(runs, now=_NOW) == "retry:2"
    runs = [
        _run("registration", days_ago=20, **_EMPTY),
        _run("retry:1", days_ago=19, **_EMPTY),
        _run("retry:2", days_ago=16, **_EMPTY),
        _run("retry:3", days_ago=8, **_EMPTY),
    ]
    assert module.next_autofill_key(runs, now=_NOW) is None  # 3회를 다 썼다


def test_followup_runs_once_after_urls_were_applied() -> None:
    runs = [_run("registration", **{**_GOOD, "followup_needed": True})]
    assert module.next_autofill_key(runs, now=_NOW) == "followup"
    runs.append(_run("followup", **_GOOD))
    assert module.next_autofill_key(runs, now=_NOW) is None


def test_failed_apply_is_reapplied_without_a_new_collection_up_to_three_times() -> None:
    run = _run("registration", **{**_GOOD, "apply_status": "FAILED", "apply_attempts": 1})
    assert module.next_autofill_key([run], now=_NOW) == f"apply:{run.id}"
    run.result_summary["apply_attempts"] = 3
    assert module.next_autofill_key([run], now=_NOW) is None


def test_a_dead_collection_claim_is_taken_over_with_the_same_key() -> None:
    running = _run("registration")
    running.result_summary = None
    running.state = "RUNNING"
    running.lease_expires_at = _NOW + timedelta(minutes=5)
    assert module.next_autofill_key([running], now=_NOW) is None  # 아직 수집 중이다
    running.lease_expires_at = _NOW - timedelta(seconds=1)
    assert module.next_autofill_key([running], now=_NOW) == "registration"


def test_a_draft_stored_without_an_apply_result_is_applied_after_the_grace() -> None:
    pending = {**_GOOD, "apply_status": "PENDING", "apply_attempts": 0}
    fresh = _run("registration", days_ago=0, **pending)
    assert module.next_autofill_key([fresh], now=_NOW) is None  # 같은 작업이 적용 중일 수 있다
    stale = _run("registration", days_ago=0.02, **pending)
    assert module.next_autofill_key([stale], now=_NOW) == f"apply:{stale.id}"
    stale.result_summary["apply_attempts"] = 3
    assert module.next_autofill_key([stale], now=_NOW) is None
    # 이 기능 이전의 '저장만' 초안(적용 결과 키 없음)은 백필 CLI 몫이다.
    legacy = _run("registration", days_ago=1, draft={"address": "서울"}, sources=[{"ok": True}])
    assert module.next_autofill_key([legacy], now=_NOW) is None


async def test_a_failed_reapply_counts_as_an_attempt(monkeypatch) -> None:
    hospital_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        operation_type="PROFILE_AUTOFILL",
        state="SUCCEEDED",
        result_summary={"draft": {"address": "서울"}, "apply_status": "FAILED", "apply_attempts": 1},
    )

    class _Session(_FakeSession):
        async def get(self, _model, _id):
            return run

    marked = []

    async def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    async def fake_mark(run_id, error):
        marked.append((run_id, error))

    monkeypatch.setattr(module, "get_async_sessionmaker", lambda: lambda: _Session(None))
    monkeypatch.setattr(module, "apply_autofill_draft", boom)
    monkeypatch.setattr(module, "_mark_apply_failed", fake_mark)

    outcome = await module._autofill_once(hospital_id, f"apply:{run.id}")

    assert outcome == {"status": "REAPPLIED", "apply_status": "FAILED"}
    assert marked == [(run.id, "RuntimeError")]


def test_dispatch_carries_the_key_and_live_flag(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(
        module.autofill_registered_hospital_profile,
        "apply_async",
        lambda **kwargs: sent.append(kwargs),
    )
    hospital_id = uuid.uuid4()
    assert module.dispatch_profile_autofill(hospital_id, "backfill") is True
    assert module.dispatch_profile_autofill(hospital_id, "backfill", allow_live=True) is True
    assert sent[0]["args"] == [str(hospital_id), "backfill"]
    assert sent[1]["args"] == [str(hospital_id), "backfill", True]
    assert sent[0]["queue"] == "default"


def test_onboarding_sweeps_survive_each_others_failures(monkeypatch) -> None:
    async def boom(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(module, "promote_completed_profiles", boom)
    monkeypatch.setattr(module, "sweep_profile_autofill", lambda: 2)

    assert module.run_onboarding_sweeps() == {
        "profiles_promoted": 0,
        "profile_autofill_dispatched": 2,
    }
