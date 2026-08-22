"""P1-3/R1 — 야간 생성 catch-up window, cap 절단 감지, 자동 발행 검증."""

import uuid
from datetime import date
from types import SimpleNamespace


class APITimeoutError(Exception):
    """Dummy anthropic.APITimeoutError for classification test."""

import arrow
import httpx
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Update

from app.models.content import ContentItem
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.models.operations import NotificationOutbox, OperationRun, OperationRunState
from app.services.content_ai_review import ContentAiReview, ContentAiReviewStatus
from app.services.essence_engine import compute_sources_snapshot_hash
from app.workers import tasks


def test_nightly_generation_stmt_covers_window_bounds():
    """야간 배치가 며칠 누락돼도 catch-up window 안의 슬롯을 다시 집어야 한다."""
    window_start = date(2026, 6, 3)
    tomorrow = date(2026, 6, 11)

    stmt = tasks._nightly_generation_stmt(window_start, tomorrow)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "scheduled_date >= '2026-06-03'" in sql
    assert "scheduled_date <= '2026-06-11'" in sql
    assert "body IS NULL" in sql
    assert "image_url IS NULL" in sql
    assert "essence_check_summary" in sql
    assert "blocking" in sql
    # cap+1로 읽어 절단 발생을 감지한다
    assert f"LIMIT {tasks.NIGHTLY_GENERATION_CAP + 1}" in sql


def test_generation_catchup_window_is_seven_days():
    """일시 장애를 사람에게 올리지 않고 일주일 동안 자동 재시도한다."""
    assert tasks.GENERATION_CATCHUP_DAYS == 7


def test_nightly_generation_recovers_legacy_ready_items_with_missing_assets():
    compiled = str(
        tasks._nightly_generation_stmt(date(2026, 8, 11), date(2026, 8, 19)).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "content_items.status IN ('DRAFT', 'REJECTED', 'READY')" in compiled


@pytest.mark.asyncio
async def test_generation_rewrites_once_with_automatic_review_feedback(monkeypatch):
    calls = []

    async def fake_generate(*_args, **kwargs):
        calls.append(kwargs.get("remediation_findings"))
        return {
            "title": "수정 전" if len(calls) == 1 else "수정 완료",
            "body": "본문",
        }

    def fake_screen(item, _philosophy):
        if item.title == "수정 전":
            return SimpleNamespace(
                status="NEEDS_REVIEW",
                summary={"blocking": True, "findings": ["피해야 할 표현을 제거하세요."]},
            )
        return SimpleNamespace(status="ALIGNED", summary={"blocking": False, "findings": []})

    async def allow_cost(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    async def reviewer_pass(**_kwargs):
        return ContentAiReview(
            status=ContentAiReviewStatus.PASS,
            confidence=0.98,
            findings=(),
            summary="통과",
            model="reviewer-test",
        )

    monkeypatch.setattr(tasks, "generate_content", fake_generate)
    monkeypatch.setattr(tasks, "screen_content_against_philosophy", fake_screen)
    monkeypatch.setattr(tasks, "review_generated_content", reviewer_pass)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allow_cost)

    content, screening = await tasks._generate_with_auto_review(
        hospital=SimpleNamespace(id=uuid.uuid4()),
        item=SimpleNamespace(content_type="FAQ", essence_check_summary=None),
        existing_titles=[],
        philosophy=SimpleNamespace(),
        approved_brief=None,
    )

    assert content["title"] == "수정 완료"
    assert calls == [[], ["피해야 할 표현을 제거하세요."]]
    assert screening.status == "ALIGNED"
    assert screening.summary["automatic_remediation_attempts"] == 1
    assert screening.summary["ai_review"]["status"] == "PASS"


@pytest.mark.asyncio
async def test_independent_ai_review_requests_one_bounded_rewrite(monkeypatch):
    generation_findings = []
    reviews = 0

    async def fake_generate(*_args, **kwargs):
        generation_findings.append(kwargs.get("remediation_findings"))
        return {"title": f"후보 {len(generation_findings)}", "body": "본문"}

    def deterministic_pass(*_args, **_kwargs):
        return SimpleNamespace(status="ALIGNED", summary={"blocking": False, "findings": []})

    async def fake_reviewer(**_kwargs):
        nonlocal reviews
        reviews += 1
        if reviews == 1:
            return ContentAiReview(
                status=ContentAiReviewStatus.REVISE,
                confidence=0.97,
                findings=("근거 없는 장비 주장을 제거하세요.",),
                summary="재작성 필요",
                model="reviewer-test",
            )
        return ContentAiReview(
            status=ContentAiReviewStatus.PASS,
            confidence=0.97,
            findings=(),
            summary="통과",
            model="reviewer-test",
        )

    async def allow_cost(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(tasks, "generate_content", fake_generate)
    monkeypatch.setattr(tasks, "screen_content_against_philosophy", deterministic_pass)
    monkeypatch.setattr(tasks, "review_generated_content", fake_reviewer)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allow_cost)

    content, screening = await tasks._generate_with_auto_review(
        hospital=SimpleNamespace(id=uuid.uuid4()),
        item=SimpleNamespace(content_type="FAQ", essence_check_summary=None),
        existing_titles=[],
        philosophy=SimpleNamespace(),
        approved_brief=None,
    )

    assert content["title"] == "후보 2"
    assert generation_findings == [[], ["근거 없는 장비 주장을 제거하세요."]]
    assert screening.status == "ALIGNED"
    assert screening.summary["reviewer_driven_rewrites"] == 1
    assert screening.summary["ai_review"]["status"] == "PASS"


@pytest.mark.asyncio
async def test_ai_reviewer_is_never_called_before_deterministic_gate_passes(monkeypatch):
    async def fake_generate(*_args, **_kwargs):
        return {"title": "차단 후보", "body": "본문"}

    def deterministic_block(*_args, **_kwargs):
        return SimpleNamespace(
            status="NEEDS_ESSENCE_REVIEW",
            summary={"blocking": True, "findings": ["하드 게이트 차단"]},
        )

    async def reviewer_must_not_run(**_kwargs):
        raise AssertionError("AI reviewer cannot override a deterministic blocker")

    async def allow_cost(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(tasks, "generate_content", fake_generate)
    monkeypatch.setattr(tasks, "screen_content_against_philosophy", deterministic_block)
    monkeypatch.setattr(tasks, "review_generated_content", reviewer_must_not_run)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allow_cost)

    _content, screening = await tasks._generate_with_auto_review(
        hospital=SimpleNamespace(id=uuid.uuid4()),
        item=SimpleNamespace(content_type="FAQ", essence_check_summary=None),
        existing_titles=[],
        philosophy=SimpleNamespace(),
        approved_brief=None,
    )

    assert screening.status == "NEEDS_ESSENCE_REVIEW"
    assert screening.summary["blocking"] is True


def test_query_priority_promotes_only_unmentioned_targets():
    successful_missing = SimpleNamespace(measurement_status="SUCCESS", is_mentioned=False)
    successful_mentioned = SimpleNamespace(measurement_status="SUCCESS", is_mentioned=True)
    failed = SimpleNamespace(measurement_status="FAILED", is_mentioned=False)

    assert tasks._desired_query_priority([successful_missing]) == "HIGH"
    assert tasks._desired_query_priority([successful_missing, successful_mentioned]) == "NORMAL"
    assert tasks._desired_query_priority([failed]) is None


def test_weekly_monitoring_commits_operation_run_before_sov_dispatch(monkeypatch):
    hospital_id = uuid.uuid4()
    hospital = SimpleNamespace(id=hospital_id, status=HospitalStatus.ACTIVE)
    dispatched: list[dict] = []
    adjusted: list[dict] = []

    class _WeeklyDB:
        def __init__(self):
            self.added = []
            self.commits = 0

        def execute(self, stmt):
            if isinstance(stmt, Update):
                for run in self.added:
                    if isinstance(run, OperationRun):
                        run.state = OperationRunState.QUEUED
                        run.queued_at = arrow.get(2026, 8, 10).datetime
                        run.version += 1
                        return _Result(scalar=run.id)
                return _Result(scalar=None)
            entity = _statement_entity(stmt)
            if entity is Hospital:
                return _Result(items=[hospital])
            if entity is OperationRun:
                return _Result(items=[])
            raise AssertionError(f"unexpected entity: {entity}")

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    db = _WeeklyDB()
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_args, **_kwargs: arrow.get(2026, 8, 10, tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(
        tasks.run_sov_for_hospital,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )
    monkeypatch.setattr(
        tasks.adjust_query_priorities,
        "apply_async",
        lambda **kwargs: adjusted.append(kwargs),
    )

    tasks.run_weekly_monitoring.run()

    runs = [value for value in db.added if isinstance(value, OperationRun)]
    assert len(runs) == 1
    run = runs[0]
    assert run.operation_type == "RUN_SOV"
    assert run.state == OperationRunState.QUEUED
    assert run.request_payload["_dispatch"]["target_id"] == str(hospital_id)
    assert run.request_payload["_dispatch"]["queue"] == "sov"
    assert dispatched == [
        {
            "args": [str(hospital_id)],
            "queue": "sov",
            "headers": {
                **tasks.build_dispatch_headers("run-sov", str(hospital_id)),
                "operation_run_id": str(run.id),
            },
            "task_id": run.task_id,
        }
    ]
    assert adjusted and adjusted[0]["queue"] == "sov"
    assert db.commits >= 2


def test_weekly_monitoring_isolates_broker_failure_and_keeps_run_requested(monkeypatch):
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    hospitals = [
        SimpleNamespace(id=first_id, status=HospitalStatus.ACTIVE),
        SimpleNamespace(id=second_id, status=HospitalStatus.ACTIVE),
    ]
    dispatched: list[str] = []
    last_success_id: uuid.UUID | None = None

    class _WeeklyDB:
        def __init__(self):
            self.added = []
            self.commits = 0

        def execute(self, stmt):
            if isinstance(stmt, Update):
                for run in self.added:
                    if isinstance(run, OperationRun) and run.hospital_id == last_success_id:
                        run.state = OperationRunState.QUEUED
                        return _Result(scalar=run.id)
                return _Result(scalar=None)
            entity = _statement_entity(stmt)
            if entity is Hospital:
                return _Result(items=hospitals)
            if entity is OperationRun:
                return _Result(items=[])
            raise AssertionError(f"unexpected entity: {entity}")

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.commits += 1

        def rollback(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def dispatch(**kwargs):
        nonlocal last_success_id
        hospital_id = kwargs["args"][0]
        if hospital_id == str(first_id):
            raise RuntimeError("broker unavailable")
        last_success_id = uuid.UUID(hospital_id)
        dispatched.append(hospital_id)

    db = _WeeklyDB()
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_args, **_kwargs: arrow.get(2026, 8, 10, tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(tasks.run_sov_for_hospital, "apply_async", dispatch)
    monkeypatch.setattr(tasks.adjust_query_priorities, "apply_async", lambda **_kwargs: None)

    tasks.run_weekly_monitoring.run()

    runs = [value for value in db.added if isinstance(value, OperationRun)]
    assert len(runs) == 2
    assert (
        next(run for run in runs if run.hospital_id == first_id).state
        == OperationRunState.REQUESTED
    )
    assert (
        next(run for run in runs if run.hospital_id == second_id).state == OperationRunState.QUEUED
    )
    assert dispatched == [str(second_id)]


def test_run_sov_operation_run_header_requires_signal_claim():
    run_id = str(uuid.uuid4())
    duplicate = SimpleNamespace(
        request=SimpleNamespace(
            headers={"operation_run_id": run_id},
            operation_run_claim_version=None,
        )
    )
    claimed = SimpleNamespace(
        request=SimpleNamespace(
            headers={"operation_run_id": run_id},
            operation_run_claim_version=7,
        )
    )
    legacy = SimpleNamespace(request=SimpleNamespace(headers={}, operation_run_claim_version=None))

    assert tasks._operation_run_claimed_or_legacy(duplicate) is False
    assert tasks._operation_run_claimed_or_legacy(claimed) is True
    assert tasks._operation_run_claimed_or_legacy(legacy) is True


def test_weekly_sov_failure_records_operator_visible_incident(monkeypatch):
    hospital_id = uuid.uuid4()
    run_id = uuid.uuid4()
    calls = []

    async def fake_open(**kwargs):
        calls.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(tasks, "open_weekly_sov_failure", fake_open)

    tasks._record_weekly_sov_failure(
        SimpleNamespace(id=hospital_id, name="테스트의원"),
        "2026-W33",
        "WEEKLY_SOV_NO_MEASUREMENT_MANIFEST",
        run_id,
    )

    assert calls == [
        {
            "hospital_id": hospital_id,
            "hospital_name": "테스트의원",
            "week_key": "2026-W33",
            "error_code": "WEEKLY_SOV_NO_MEASUREMENT_MANIFEST",
            "operation_run_id": run_id,
        }
    ]


def test_weekly_manifest_without_failed_cells_is_resolved_only_when_complete_or_excluded():
    resolved = SimpleNamespace(
        cells=[
            SimpleNamespace(state="SUCCESS"),
            SimpleNamespace(state="EXCLUDED"),
        ]
    )
    unresolved = SimpleNamespace(
        cells=[
            SimpleNamespace(state="SUCCESS"),
            SimpleNamespace(state="PENDING"),
        ]
    )
    empty = SimpleNamespace(cells=[])

    assert tasks._weekly_manifest_is_resolved(resolved) is True
    assert tasks._weekly_manifest_is_resolved(unresolved) is False
    assert tasks._weekly_manifest_is_resolved(empty) is False


def test_weekly_manifest_blocks_provider_execution_when_frozen_policy_drifted():
    protocol = tasks.sov_engine.measurement_protocol()
    manifest = SimpleNamespace(
        configured_platforms=["chatgpt"],
        platform_provenance={
            "measurement_protocol": {**protocol, "openai_model_query": "prior-model"}
        },
    )

    assert tasks._manifest_execution_policy_matches(manifest) is False


def test_weekly_manifest_ignores_unused_provider_drift():
    protocol = tasks.sov_engine.measurement_protocol()
    manifest = SimpleNamespace(
        configured_platforms=["chatgpt"],
        platform_provenance={
            "measurement_protocol": {**protocol, "gemini_model": "unused-prior-model"}
        },
    )

    assert tasks._manifest_execution_policy_matches(manifest) is True


def test_weekly_manifest_without_a_platform_fails_closed():
    manifest = SimpleNamespace(
        configured_platforms=[],
        platform_provenance={"measurement_protocol": tasks.sov_engine.measurement_protocol()},
    )

    assert tasks._manifest_execution_policy_matches(manifest) is False


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (TimeoutError("secret-token"), "PROVIDER_TIMEOUT"),
        (ConnectionError("secret-token"), "PROVIDER_UNAVAILABLE"),
        (ValueError("secret-token"), "GENERATION_REJECTED"),
        (APITimeoutError("secret-token"), "PROVIDER_TIMEOUT"),
        (RuntimeError("secret-token"), "GENERATION_FAILED"),
    ],
)
def test_generation_failure_classification_never_persists_exception_text(error, expected_code):
    code, message = tasks.classify_generation_failure(error)

    assert code == expected_code
    assert str(error) not in message
    assert "운영 센터" in message


def test_fatal_generation_failures_still_request_immediate_slack():
    for code in (
        "COST_BLOCKED",
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "GENERATION_REJECTED",
        "GENERATION_FAILED",
    ):
        assert tasks.generation_notify_requested(code)
    assert not tasks.generation_notify_requested("MISSING_APPROVED_ESSENCE")


class _NightlyTaskDB:
    def __init__(self):
        self.added = []
        self.commits = 0

    class _QueryResult:
        def all(self):
            return []

        def scalar_one_or_none(self):
            return None

    def execute(self, _statement):
        return self._QueryResult()

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def expire_all(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _NightlyTaskRecorder:
    def __init__(self, db, *_args):
        self.db = db
        self.run = SimpleNamespace(id=uuid.uuid4())

    def record(self, *_args, **_kwargs):
        return None

    def item_run(self, *_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    def finish(self):
        self.db.commit()


def _nightly_item(hospital_name: str):
    hospital = SimpleNamespace(id=uuid.uuid4(), name=hospital_name)
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital=hospital,
        hospital_id=hospital.id,
        generation_claimed_at=None,
        content_philosophy_id=None,
        essence_status=None,
        essence_check_summary=None,
    )


def _patch_nightly_task_shell(monkeypatch, db, items, cycle_date):
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "GenerationBatchRecorder", _NightlyTaskRecorder)
    monkeypatch.setattr(
        tasks, "_load_nightly_generation_batch", lambda *_args: (items, 0)
    )
    monkeypatch.setattr(tasks, "load_stuck_claims", lambda *_args: [])
    monkeypatch.setattr(tasks, "release_unfinished_claims", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: arrow.get(cycle_date, tzinfo="Asia/Seoul"),
    )


def test_nightly_cost_blocked_opens_incident_with_notify_true(monkeypatch):
    cycle_date = date(2026, 8, 19)
    db = _NightlyTaskDB()
    item = _nightly_item("비용차단의원")
    incident_calls = []

    async def block_cost(*_args, **_kwargs):
        return SimpleNamespace(allowed=False, reason="daily cap")

    async def capture_incident(**kwargs):
        incident_calls.append(kwargs)

    _patch_nightly_task_shell(monkeypatch, db, [item], cycle_date)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: SimpleNamespace()
    )
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", block_cost)
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)

    tasks.nightly_content_generation.run()

    assert len(incident_calls) == 1
    assert incident_calls[0]["code"] == "COST_BLOCKED"
    assert incident_calls[0]["notify"] is True


def test_nightly_classified_fatal_failure_opens_incident_with_notify_true(monkeypatch):
    cycle_date = date(2026, 8, 19)
    db = _NightlyTaskDB()
    item = _nightly_item("공급자지연의원")
    incident_calls = []

    async def allow_cost(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    async def capture_incident(**kwargs):
        incident_calls.append(kwargs)

    def provider_timeout(*_args, **_kwargs):
        raise TimeoutError("provider details must stay private")

    _patch_nightly_task_shell(monkeypatch, db, [item], cycle_date)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: SimpleNamespace()
    )
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allow_cost)
    monkeypatch.setattr(tasks, "prepare_automatic_content_brief_sync", provider_timeout)
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)

    tasks.nightly_content_generation.run()

    assert len(incident_calls) == 1
    assert incident_calls[0]["code"] == "PROVIDER_TIMEOUT"
    assert incident_calls[0]["notify"] is True


def test_nightly_missing_essence_is_silent_per_hospital_and_one_digest(monkeypatch):
    cycle_date = date(2026, 8, 19)
    db = _NightlyTaskDB()
    items = [_nightly_item("첫번째의원"), _nightly_item("두번째의원")]
    incident_calls = []

    async def capture_incident(**kwargs):
        incident_calls.append(kwargs)

    _patch_nightly_task_shell(monkeypatch, db, items, cycle_date)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: None
    )
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)

    tasks.nightly_content_generation.run()

    assert len(incident_calls) == 2
    assert {call["code"] for call in incident_calls} == {"MISSING_APPROVED_ESSENCE"}
    assert not any(call["notify"] for call in incident_calls)
    outbox = [value for value in db.added if isinstance(value, NotificationOutbox)]
    assert len(outbox) == 1
    assert outbox[0].notification_type == "MISSING_APPROVED_ESSENCE_DIGEST"
    assert outbox[0].dedupe_key == (
        f"MISSING_APPROVED_ESSENCE_DIGEST:{cycle_date.isoformat()}"
    )
    visible = str(outbox[0].payload)
    assert "온보딩 병원 2곳 · 글 2건" in visible
    assert "승인 기준이 없어 생성을 건너뜀" in visible


def test_auto_publish_block_alert_key_changes_only_when_reason_changes():
    content_id = uuid.uuid4()

    initial = tasks._auto_publish_block_alert_key(
        content_id, "2026-08-09", "ESSENCE_NOT_ALIGNED", "운영 기준 미승인"
    )
    unchanged = tasks._auto_publish_block_alert_key(
        content_id, "2026-08-09", "ESSENCE_NOT_ALIGNED", "운영 기준 미승인"
    )
    corrected_but_still_blocked = tasks._auto_publish_block_alert_key(
        content_id, "2026-08-09", "ESSENCE_NOT_ALIGNED", "피해야 할 표현 포함"
    )

    assert initial == unchanged
    assert initial != corrected_but_still_blocked


def test_nightly_generation_stmt_filters_hospital_status():
    """야간 생성은 공개 활성화된 병원만 대상 — 미공개 병원 비용 발생 방지."""
    stmt = tasks._nightly_generation_stmt(date(2026, 6, 3), date(2026, 6, 11))
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "JOIN hospitals" in sql
    assert "'ACTIVE'" in sql
    assert "hospitals.site_live IS true" in sql
    # PENDING_DOMAIN/PAUSED/ONBOARDING 은 IN 목록에 없어야 한다.
    assert "'PENDING_DOMAIN'" not in sql
    assert "'PAUSED'" not in sql


def test_load_stuck_claims_filters_live_active_hospitals():
    """stuck claim 복구 감지도 실제 생성 대상과 같은 공개 활성화 병원만 본다."""
    sql = str(
        tasks._stuck_claims_stmt(date(2026, 6, 3), date(2026, 6, 11)).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "JOIN hospitals" in sql
    assert "'ACTIVE'" in sql
    assert "hospitals.site_live IS true" in sql
    assert "'PENDING_DOMAIN'" not in sql
    assert "'PAUSED'" not in sql


# ── 결함 5: 콘텐츠 허브 공개 URL — 존재하지 않던 하드코딩 preview 도메인 제거 ──


def test_build_aeo_site_has_autoretry(monkeypatch):
    """STEP4 허브 준비 태스크는 일시 장애 시 재시도돼야 한다 (결함 10)."""
    assert tasks.build_aeo_site.max_retries == 3
    assert Exception in tasks.build_aeo_site.autoretry_for


@pytest.mark.parametrize(
    ("profile_complete", "v0_report_done", "expected"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_site_build_requires_profile_and_v0(profile_complete, v0_report_done, expected):
    hospital = SimpleNamespace(
        profile_complete=profile_complete,
        v0_report_done=v0_report_done,
    )
    assert tasks._site_build_prerequisites_met(hospital) is expected


def test_custom_domain_https_health_contract():
    hospital_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "healthy.example.com":
            return httpx.Response(
                200,
                json={
                    "hospital_id": str(hospital_id),
                    "slug": "healthy-clinic",
                    "canonical_host": "healthy.example.com",
                    "release": "site-r17",
                },
            )
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        assert tasks._check_custom_domain_https(
            client,
            "healthy.example.com",
            expected_hospital_id=hospital_id,
            expected_slug="healthy-clinic",
        ) == (
            True,
            "tenant_marker_ok",
        )
        assert tasks._check_custom_domain_https(
            client,
            "broken.example.com",
            expected_hospital_id=hospital_id,
            expected_slug="healthy-clinic",
        ) == (
            False,
            "http_503",
        )


def test_public_site_url_prefers_aeo_domain():
    assert (
        tasks._public_site_url("clinic.example.com", "jangpyeonhan")
        == "https://clinic.example.com/"
    )


def test_public_site_url_falls_back_to_platform_subdomain(monkeypatch):
    monkeypatch.setattr(tasks.settings, "SITE_BASE_URL", "https://reputation.motionlabs.kr")
    assert (
        tasks._public_site_url(None, "jangpyeonhan")
        == "https://jangpyeonhan.reputation.motionlabs.kr/"
    )
    assert "preview.motionlabs.io" not in tasks._public_site_url(None, "jangpyeonhan")


# ── 결함 7: priority 게이팅 + HIGH 상한 ──


def test_priority_included_gating_rules():
    # HIGH: 항상 / NORMAL: 짝수주만 / LOW: 월초만
    assert tasks._priority_included("HIGH", is_even_week=False, is_month_start=False) is True
    assert tasks._priority_included("NORMAL", is_even_week=True, is_month_start=False) is True
    assert tasks._priority_included("NORMAL", is_even_week=False, is_month_start=False) is False
    assert tasks._priority_included("LOW", is_even_week=False, is_month_start=True) is True
    assert tasks._priority_included("LOW", is_even_week=False, is_month_start=False) is False


def test_apply_high_priority_cap_trims_excess_high_specs():
    specs = [{"priority": "HIGH", "n": i} for i in range(5)] + [{"priority": "NORMAL", "n": 99}]
    kept, dropped = tasks._apply_high_priority_cap(specs, cap=3)

    assert dropped == 2
    high_kept = [s for s in kept if s["priority"] == "HIGH"]
    assert len(high_kept) == 3
    # 결정론적: 앞에서부터 유지
    assert [s["n"] for s in high_kept] == [0, 1, 2]
    # NORMAL은 상한과 무관하게 유지
    assert any(s["priority"] == "NORMAL" for s in kept)


class _SpecDB:
    def __init__(self, query_matrices):
        self._qm = {qm.id: qm for qm in query_matrices}

    def get(self, _model, obj_id):
        return self._qm.get(obj_id)


def _variant(vid, qm_id, platform="CHATGPT"):
    return SimpleNamespace(
        id=vid, query_matrix_id=qm_id, platform=platform, query_text=f"Q-{vid}", is_active=True
    )


def _qm(qm_id):
    return SimpleNamespace(id=qm_id, hospital_id="h1")


def test_build_measurement_specs_gates_target_priority(monkeypatch):
    """target/variant 유래 spec도 target.priority 기준으로 주간 게이팅돼야 한다 (결함 7)."""
    hospital = SimpleNamespace(id="h1")
    qm_high, qm_normal = _qm("qm-high"), _qm("qm-normal")
    target_high = SimpleNamespace(
        id="t-high", priority="HIGH", variants=[_variant("v-high", "qm-high")]
    )
    target_normal = SimpleNamespace(
        id="t-normal", priority="NORMAL", variants=[_variant("v-normal", "qm-normal")]
    )
    db = _SpecDB([qm_high, qm_normal])
    monkeypatch.setattr(tasks.settings, "GEMINI_API_KEY", "")

    # 홀수 주차(is_even_week=False), 월초 아님 → NORMAL target 제외, HIGH만 포함
    specs, trimmed = tasks._build_measurement_specs(
        db=db,
        hospital=hospital,
        query_targets=[target_high, target_normal],
        fallback_queries=[],
        is_even_week=False,
        is_month_start=False,
        high_priority_cap=30,
    )

    assert trimmed == 0
    target_ids = {s["target_id"] for s in specs}
    assert target_ids == {"t-high"}


def test_build_measurement_specs_applies_high_cap(monkeypatch):
    hospital = SimpleNamespace(id="h1")
    qms = [_qm(f"qm-{i}") for i in range(5)]
    targets = [
        SimpleNamespace(id=f"t-{i}", priority="HIGH", variants=[_variant(f"v-{i}", f"qm-{i}")])
        for i in range(5)
    ]
    db = _SpecDB(qms)
    monkeypatch.setattr(tasks.settings, "GEMINI_API_KEY", "")

    specs, trimmed = tasks._build_measurement_specs(
        db=db,
        hospital=hospital,
        query_targets=targets,
        fallback_queries=[],
        is_even_week=True,
        is_month_start=True,
        high_priority_cap=2,
    )

    assert trimmed == 3
    assert len(specs) == 2


def test_nightly_generation_orders_carried_over_items_first():
    """전월 이월(carried_over_from) 슬롯이 cap 안에서 가장 먼저 생성돼야 한다."""
    stmt = tasks._nightly_generation_stmt(date(2026, 7, 1), date(2026, 7, 2))
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

    order_clause = sql.split("ORDER BY", 1)[1]
    assert "carried_over_from IS NOT NULL DESC" in order_clause
    # 이월 우선 정렬이 발행 예정일 정렬보다 앞선다.
    assert order_clause.index("carried_over_from") < order_clause.index("scheduled_date")


def test_nightly_generation_stmt_uses_row_level_claiming():
    stmt = tasks._nightly_generation_stmt(date(2026, 7, 1), date(2026, 7, 2))
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql
    assert "generation_claimed_at IS NULL" in sql


def test_generate_single_content_item_stays_draft_until_manual_publish(monkeypatch):
    item = SimpleNamespace(
        id="content-1",
        hospital_id="hospital-1",
        content_type=SimpleNamespace(value="FAQ"),
        title=None,
        body=None,
        meta_description=None,
        image_url="gs://bucket/existing.png",
        image_prompt=None,
        generated_at=None,
        body_updated_at=None,
        status=None,
        published_at=None,
        published_by=None,
        content_philosophy_id=None,
        brief_status=None,
        content_brief=None,
        essence_status=None,
        essence_check_summary=None,
        references_list=None,
        faq_question=None,
        faq_answer_summary=None,
    )
    hospital = SimpleNamespace(id="hospital-1", slug="test-clinic")
    philosophy = SimpleNamespace(id="philosophy-1")

    class _ExistingTitles:
        def all(self):
            return []

    class _ApprovedPhilosophy:
        def scalar_one_or_none(self):
            return philosophy

    class _WriteBackResult:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class _GenerationDB:
        """조회는 순서대로, 쓰기는 문장 종류로 분기하는 fake.

        생성 결과는 더 이상 추적 객체 변경이 아니라 **조건부 UPDATE**로 쓰인다
        (생성 중 취소된 슬롯이 되살아나지 않도록). 그래서 이 fake도 UPDATE를 인식해야 한다.
        """

        def __init__(self):
            self._results = [_ExistingTitles(), _ApprovedPhilosophy()]
            self.commit_calls = 0
            self.written_values = []

        def execute(self, stmt):
            if isinstance(stmt, Update):
                self.written_values.append(dict(stmt._values or {}))
                return _WriteBackResult(1)
            return self._results.pop(0)

        def commit(self):
            self.commit_calls += 1

        def refresh(self, _obj):
            pass

    async def fake_generate_content(*_args, **_kwargs):
        return {
            "title": "치질 수술 전 확인할 점",
            "body": "환자 상태에 따라 진료 방향을 설명합니다.",
            "meta_description": "진료 전 확인할 점.",
            "references": [{"title": "질병관리청", "url": "https://www.kdca.go.kr/example"}],
            "faq_question": "치질 수술 전 무엇을 확인하나요?",
            "faq_answer_summary": "증상 단계와 회복 계획을 함께 확인합니다.",
        }

    monkeypatch.setattr(tasks, "generate_content", fake_generate_content)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "screen_content_against_philosophy",
        lambda _item, _philosophy: SimpleNamespace(status="ALIGNED", summary={"ok": True}),
    )

    db = _GenerationDB()
    tasks._generate_single_content_item(db, item, hospital)

    # 생성 결과는 조건부 UPDATE로 쓰인다 — 추적 객체를 직접 바꾸지 않는다.
    assert db.written_values, "생성 결과가 write-back되지 않았다"
    written = db.written_values[0]
    # SQLAlchemy는 .values()의 값을 BindParameter로 감싼다 — 실제 값을 꺼낸다.
    values = {
        str(getattr(col, "key", col)): getattr(val, "value", val) for col, val in written.items()
    }
    assert values["status"] == tasks.ContentStatus.DRAFT
    assert values["content_philosophy_id"] == philosophy.id
    assert values["generated_at"] is not None
    # 재생성은 발행 상태를 만들지 않는다 — 발행은 별도 게이트를 거쳐야 한다.
    assert "published_at" not in values
    assert "published_by" not in values
    assert item.published_at is None
    assert item.published_by is None


def test_unapproved_essence_skips_before_cost_or_provider_call(monkeypatch):
    item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        content_philosophy_id=uuid.uuid4(),
        essence_status=None,
        essence_check_summary=None,
    )
    hospital = SimpleNamespace(id=item.hospital_id, name="준비중의원")

    class ExistingTitles:
        def all(self):
            return []

    class DB:
        def __init__(self):
            self.commit_calls = 0

        def execute(self, _statement):
            return ExistingTitles()

        def commit(self):
            self.commit_calls += 1

    async def forbidden_call(*_args, **_kwargs):
        raise AssertionError("승인 전에는 공급자/비용 가드를 호출하면 안 된다")

    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: None)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", forbidden_call)
    monkeypatch.setattr(tasks, "generate_content", forbidden_call)

    outcome, code, _message = tasks._generate_single_content_item(DB(), item, hospital)

    assert outcome == tasks.GenerationItemState.SKIPPED
    assert code == "MISSING_APPROVED_ESSENCE"
    assert item.content_philosophy_id is None
    assert item.essence_status == tasks.ESSENCE_STATUS_MISSING_APPROVED
    assert item.essence_check_summary["blocking"] is True


def test_approved_hospital_generates_when_readiness_would_be_pending(monkeypatch):
    """마지막 승인 철학이 있으면 current PENDING이어도 생성하고, 온보딩만 MISSING으로 건너뛴다."""
    hospital = SimpleNamespace(id=uuid.uuid4(), name="승인의원", slug="approved-clinic")
    processed = SimpleNamespace(
        id=uuid.uuid4(),
        source_type=SourceType.NAVER_BLOG,
        content_hash="approved-baseline",
        status=SourceStatus.PROCESSED,
        processed_at=arrow.get(2026, 8, 18).datetime,
    )
    pending = SimpleNamespace(
        id=uuid.uuid4(),
        source_type=SourceType.NAVER_BLOG,
        raw_text="새로 추가된 네이버 텍스트 자료",
        content_hash="pending-extra",
        status=SourceStatus.PENDING,
        processed_at=None,
    )
    approved = SimpleNamespace(
        id=uuid.uuid4(),
        status=PhilosophyStatus.APPROVED,
        source_snapshot_hash=compute_sources_snapshot_hash([processed]),
        source_asset_ids=[processed.id],
    )

    class ExistingTitles:
        def all(self):
            return []

    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class SourcesResult:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class WriteBackResult:
        rowcount = 1

    class DB:
        def __init__(self, philosophy, sources):
            self.philosophy = philosophy
            self.sources = sources
            self.written_values = []
            self.item = None

        def execute(self, statement):
            if isinstance(statement, Update):
                values = {
                    str(getattr(column, "key", column)): getattr(value, "value", value)
                    for column, value in (statement._values or {}).items()
                }
                self.written_values.append(values)
                return WriteBackResult()
            entity = _statement_entity(statement)
            if entity is ContentItem:
                return ExistingTitles()
            if entity is HospitalContentPhilosophy:
                return ScalarResult(self.philosophy)
            if entity is HospitalSourceAsset:
                return SourcesResult(self.sources)
            raise AssertionError(f"unexpected entity: {entity}")

        def commit(self):
            return None

        def refresh(self, item):
            for key, value in self.written_values[-1].items():
                setattr(item, key, value)

    calls = {"cost": 0, "generate": 0}

    async def allow_cost(*_args, **_kwargs):
        calls["cost"] += 1
        return SimpleNamespace(allowed=True)

    async def fake_generate_content(*_args, **_kwargs):
        calls["generate"] += 1
        return {
            "title": "치질 수술 전 확인할 점",
            "body": "환자 상태에 따라 진료 방향을 설명합니다.",
            "meta_description": "진료 전 확인할 점.",
            "references": [
                {"title": "질병관리청", "url": "https://www.kdca.go.kr/example"}
            ],
            "faq_question": "치질 수술 전 무엇을 확인하나요?",
            "faq_answer_summary": "증상 단계와 회복 계획을 함께 확인합니다.",
        }

    async def reviewer_pass(**_kwargs):
        return ContentAiReview(
            status=ContentAiReviewStatus.PASS,
            confidence=0.98,
            findings=(),
            summary="통과",
            model="reviewer-test",
        )

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allow_cost)
    monkeypatch.setattr(tasks, "generate_content", fake_generate_content)
    monkeypatch.setattr(tasks, "review_generated_content", reviewer_pass)
    monkeypatch.setattr(
        tasks,
        "screen_content_against_philosophy",
        lambda _item, _philosophy: SimpleNamespace(
            status="ALIGNED", summary={"blocking": False, "findings": []}
        ),
    )
    monkeypatch.setattr(
        tasks,
        "assess_content_publication",
        lambda _item, philosophy: SimpleNamespace(
            publishable=True,
            code=None,
            message=None,
            violations=(),
            essence_status="ALIGNED",
            essence_summary={"blocking": False, "findings": []},
            philosophy_id=philosophy.id,
        ),
    )

    pending_item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        content_type=SimpleNamespace(value="FAQ"),
        title=None,
        body=None,
        meta_description=None,
        image_url="gs://bucket/existing.png",
        image_prompt=None,
        generated_at=None,
        body_updated_at=None,
        status=tasks.ContentStatus.DRAFT,
        published_at=None,
        published_by=None,
        content_philosophy_id=None,
        brief_status=None,
        content_brief=None,
        essence_status=None,
        essence_check_summary=None,
        references_list=None,
        faq_question=None,
        faq_answer_summary=None,
    )
    approved_db = DB(approved, [processed, pending])
    outcome, code, _message = tasks._generate_single_content_item(
        approved_db, pending_item, hospital
    )
    assert outcome == tasks.GenerationItemState.SUCCEEDED
    assert code is None
    assert calls["cost"] == 1
    assert calls["generate"] == 1
    assert approved_db.written_values
    written = approved_db.written_values[0]
    assert written["title"] == "치질 수술 전 확인할 점"
    assert written["body"] == "환자 상태에 따라 진료 방향을 설명합니다."
    assert written["status"] == tasks.ContentStatus.DRAFT
    assert written["content_philosophy_id"] == approved.id
    assert pending_item.essence_status != tasks.ESSENCE_STATUS_MISSING_APPROVED

    calls_before_onboarding = dict(calls)
    onboarding_item = SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        content_philosophy_id=uuid.uuid4(),
        essence_status=None,
        essence_check_summary=None,
    )
    outcome, code, _message = tasks._generate_single_content_item(
        DB(None, []), onboarding_item, hospital
    )
    assert outcome == tasks.GenerationItemState.SKIPPED
    assert code == "MISSING_APPROVED_ESSENCE"
    assert onboarding_item.content_philosophy_id is None
    assert onboarding_item.essence_status == tasks.ESSENCE_STATUS_MISSING_APPROVED
    assert calls == calls_before_onboarding


def test_content_item_schedule_slots_have_db_uniqueness():
    indexes = {index.name: index for index in ContentItem.__table__.indexes}
    slot_index = indexes.get("uq_content_items_schedule_slot")

    assert slot_index is not None
    assert slot_index.unique is True


def test_v0_report_requires_at_least_one_successful_measurement():
    with pytest.raises(RuntimeError, match="성공 측정 결과가 없습니다"):
        tasks._ensure_v0_has_successful_measurements(success_count=0, failure_count=5)


def test_v0_failure_summary_preserves_platform_and_safe_cause():
    summary = tasks._classify_v0_failure(
        tasks.Counter(
            {
                "chatgpt:provider_query_failed:AuthenticationError": 75,
                "gemini:provider_query_failed:PermissionDenied": 75,
            }
        ),
        {
            "chatgpt": tasks.Counter({"FAILED": 75}),
            "gemini": tasks.Counter({"FAILED": 75}),
        },
    )

    assert summary["safe_error_code"] == "V0_PROVIDER_AUTH_OR_MODEL"
    assert summary["failed_count"] == 150
    assert summary["platforms"]["chatgpt"]["failure_count"] == 75
    assert summary["platforms"]["gemini"]["failure_count"] == 75


def test_v0_quota_failure_opens_platform_circuit_and_reports_skipped_work():
    reason = "provider_query_failed:RateLimitError:http_429:credit_balance_exhausted"
    results = [
        {"measurement_status": "FAILED", "failure_reason": reason}
        for _ in range(tasks.V0_REPEAT_COUNT)
    ]

    assert tasks._provider_batch_outage_reason(results) == reason

    summary = tasks._classify_v0_failure(
        tasks.Counter({f"chatgpt:{reason}": tasks.V0_REPEAT_COUNT}),
        {"chatgpt": tasks.Counter({"FAILED": tasks.V0_REPEAT_COUNT})},
        planned_counts={"chatgpt": 75},
        blocked_platforms={"chatgpt": reason},
    )

    assert summary["safe_error_code"] == "V0_PROVIDER_QUOTA_EXHAUSTED"
    assert summary["platforms"]["chatgpt"] == {
        "success_count": 0,
        "failure_count": tasks.V0_REPEAT_COUNT,
        "attempted_count": tasks.V0_REPEAT_COUNT,
        "planned_count": 75,
        "skipped_count": 75 - tasks.V0_REPEAT_COUNT,
        "blocked_reason": reason,
        "failure_reasons": {reason: tasks.V0_REPEAT_COUNT},
    }


def test_v0_partial_success_does_not_tell_operator_to_rerun_completed_onboarding():
    reason = "provider_query_failed:ClientError:http_429:resource_exhausted"
    summary = tasks._classify_v0_failure(
        tasks.Counter({f"gemini:{reason}": 5}),
        {
            "chatgpt": tasks.Counter({"SUCCESS": 75}),
            "gemini": tasks.Counter({"FAILED": 5}),
        },
        planned_counts={"chatgpt": 75, "gemini": 75},
        blocked_platforms={"gemini": reason},
    )

    assert summary["safe_error_code"] == "V0_PARTIAL_PROVIDER_DEGRADED"
    assert "다시 실행하지 마세요" in summary["next_action"]


def test_v0_parse_failures_do_not_open_provider_circuit():
    results = [
        {"measurement_status": "FAILED", "failure_reason": "mention_parse_failed"}
        for _ in range(tasks.V0_REPEAT_COUNT)
    ]

    assert tasks._provider_batch_outage_reason(results) is None


def test_monthly_report_failures_are_raised_for_celery_autoretry():
    with pytest.raises(RuntimeError, match="월간 리포트 실패"):
        tasks._raise_if_monthly_report_failures([("장편한외과의원", RuntimeError("pdf boom"))])


class _NestedSlotTransaction:
    def __init__(self, db):
        self._db = db

    def __enter__(self):
        self._db._staged = []
        return self

    def __exit__(self, exc_type, *_exc):
        if exc_type is None:
            self._db.persisted.extend(self._db._staged)
        self._db._staged = None
        return False


class _MonthlySlotDB:
    def __init__(self, schedules):
        # 2~3번째 execute는 스케줄별 기존 계획 슬롯 순번 조회(scalars().all()) 이다.
        self._results = [_Result(items=schedules), _Result(items=[]), _Result(items=[])]
        self.execute_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.persisted = []
        self._staged = None

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result

    def begin_nested(self):
        return _NestedSlotTransaction(self)

    def add(self, item):
        assert self._staged is not None
        self._staged.append(item)

    def flush(self):
        self.flush_calls += 1
        if self.flush_calls == 2:
            raise IntegrityError("insert", {}, Exception("duplicate slot"))

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1
        self.persisted.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_monthly_slot_generation_isolates_valueerror_and_opens_incident(monkeypatch):
    """발행요일이 적은 스케줄이 generate_monthly_slots ValueError를 내도 이전 병원 슬롯은
    유지되고, 이후 병원 처리도 계속되며, 실패 병원은 durable incident/outbox로 남는다."""
    hospitals = [
        SimpleNamespace(id="h1", name="첫번째의원", status=HospitalStatus.ACTIVE),
        SimpleNamespace(id="h2", name="문제의원", status=HospitalStatus.ACTIVE),
    ]
    schedules = [
        SimpleNamespace(
            id="s1",
            hospital_id=hospitals[0].id,
            hospital=hospitals[0],
            plan="PLAN_12",
            publish_days=[0, 2],
            active_from=date(2026, 1, 1),
        ),
        SimpleNamespace(
            id="s2",
            hospital_id=hospitals[1].id,
            hospital=hospitals[1],
            plan="PLAN_12",
            publish_days=[1],
            active_from=date(2026, 1, 1),
        ),
    ]
    db = _MonthlySlotDB(schedules)

    calls = {"n": 0}

    def fake_generate(plan, publish_days, next_month, start_date=None):
        calls["n"] += 1
        if publish_days == [1]:
            raise ValueError("발행요일 대비 편수가 과다")
        return [(date(2026, 3, 2), "FAQ", 1, 1)]

    opened: list[dict] = []
    recovered: list[dict] = []

    async def fake_open(**kwargs):
        opened.append(kwargs)
        return uuid.uuid4()

    async def fake_recover(**kwargs):
        recovered.append(kwargs)
        return False

    # 2월(28일) 다음 달 슬롯 생성 상황 — 25일 트리거.
    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_a, **_k: arrow.get(2026, 2, 25, tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "open_monthly_slot_failure", fake_open)
    monkeypatch.setattr(tasks, "recover_monthly_slot_failure", fake_recover)
    monkeypatch.setattr("app.workers.monthly_slots.generate_monthly_slots", fake_generate)

    tasks.monthly_slot_generation()

    # h1은 커밋됐고, h2 실패는 루프를 죽이지 않았다.
    assert calls["n"] == 2
    assert db.commit_calls == 1
    assert len(db.persisted) == 1
    assert db.persisted[0].hospital_id == "h1"
    assert recovered == [{"hospital_id": "h1", "period_key": "2026-03"}]
    assert len(opened) == 1
    assert opened[0]["hospital_id"] == "h2"
    assert opened[0]["hospital_name"] == "문제의원"


def test_monthly_slot_generation_keeps_prior_success_when_later_schedule_conflicts(monkeypatch):
    hospitals = [
        SimpleNamespace(id="h1", name="첫번째의원", status=HospitalStatus.ACTIVE),
        SimpleNamespace(id="h2", name="두번째의원", status=HospitalStatus.ACTIVE),
    ]
    schedules = [
        SimpleNamespace(
            id="s1",
            hospital_id=hospitals[0].id,
            hospital=hospitals[0],
            plan="PLAN_4",
            publish_days=[0],
            active_from=date(2026, 1, 1),
        ),
        SimpleNamespace(
            id="s2",
            hospital_id=hospitals[1].id,
            hospital=hospitals[1],
            plan="PLAN_4",
            publish_days=[0],
            active_from=date(2026, 1, 1),
        ),
    ]
    db = _MonthlySlotDB(schedules)

    monkeypatch.setattr(
        tasks.arrow, "now", lambda *_args, **_kwargs: arrow.get(2026, 6, 25, tzinfo="Asia/Seoul")
    )
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: None)
    monkeypatch.setattr(tasks, "open_monthly_slot_failure", lambda **_kwargs: None)

    async def fake_recover(**_kwargs):
        return False

    monkeypatch.setattr(tasks, "recover_monthly_slot_failure", fake_recover)
    monkeypatch.setattr(
        "app.workers.monthly_slots.generate_monthly_slots",
        lambda *_args, **_kwargs: [(date(2026, 7, 1), "FAQ", 1, 1)],
    )

    tasks.monthly_slot_generation()

    assert db.rollback_calls == 0
    assert db.commit_calls == 1
    assert len(db.persisted) == 1
    assert db.persisted[0].hospital_id == "h1"


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, items=None, scalar=None):
        self._items = items
        self._scalar = scalar

    def scalars(self):
        return _Scalars(self._items)

    def scalar_one(self):
        return self._scalar

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        if self._items is not None:
            return self._items[0] if self._items else None
        return self._scalar


class _FakeSyncDB:
    """첫 execute는 batch 조회, 두 번째 execute는 overflow count 조회."""

    def __init__(self, items, total_count):
        self._results = [_Result(items=items), _Result(scalar=total_count)]
        self.execute_calls = 0
        self.commit_calls = 0

    def execute(self, _stmt):
        result = self._results[self.execute_calls]
        self.execute_calls += 1
        return result

    def commit(self):
        self.commit_calls += 1


def _items(n):
    return [SimpleNamespace(id=i) for i in range(n)]


def test_load_nightly_generation_batch_without_truncation():
    db = _FakeSyncDB(_items(3), total_count=3)

    items, truncated = tasks._load_nightly_generation_batch(
        db, date(2026, 6, 10), date(2026, 6, 11)
    )

    assert len(items) == 3
    assert truncated == 0
    assert db.execute_calls == 1  # overflow count 조회 불필요
    assert db.commit_calls == 1
    assert all(item.generation_claimed_at is not None for item in items)


def test_load_nightly_generation_batch_detects_cap_truncation():
    cap = tasks.NIGHTLY_GENERATION_CAP
    db = _FakeSyncDB(_items(cap + 1), total_count=cap + 7)

    items, truncated = tasks._load_nightly_generation_batch(
        db, date(2026, 6, 10), date(2026, 6, 11)
    )

    assert len(items) == cap
    assert truncated == 7  # 정확한 잔여 건수 보고
    assert db.execute_calls == 2
    assert db.commit_calls == 1
    assert all(item.generation_claimed_at is not None for item in items)


# ── 08:00 자동 발행: due/public 상태와 예외 필터 ──


def test_auto_publish_due_statement_includes_missing_body_for_blocker_projection():
    sql = str(
        tasks._auto_publish_due_stmt(date(2026, 6, 10)).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "content_items.status IN ('DRAFT', 'READY')" in sql
    assert "content_items.body IS NOT NULL" not in sql
    assert "hospitals.status = 'ACTIVE'" in sql
    assert "hospitals.site_live IS true" in sql
    assert "content_items.scheduled_date >= '2026-06-03'" in sql
    assert "content_items.scheduled_date <= '2026-06-10'" in sql


def test_morning_publish_cycle_enqueues_one_digest_without_per_item_rows(monkeypatch):
    cycle_date = date(2026, 8, 19)
    content_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    first_hospital_id = uuid.uuid4()
    second_hospital_id = uuid.uuid4()
    outcomes = {
        content_ids[0]: {
            "kind": "published",
            "hospital_id": first_hospital_id,
            "hospital_name": "첫번째의원",
            "slug": "first",
            "aeo_domain": None,
            "treatments": [],
        },
        content_ids[1]: {
            "kind": "published",
            "hospital_id": first_hospital_id,
            "hospital_name": "첫번째의원",
            "slug": "first",
            "aeo_domain": None,
            "treatments": [],
        },
        content_ids[2]: {
            "kind": "published",
            "hospital_id": second_hospital_id,
            "hospital_name": "두번째의원",
            "slug": "second",
            "aeo_domain": None,
            "treatments": [],
        },
    }

    class CycleDB:
        def __init__(self, *, due=False):
            self.due = due
            self.added = []
            self.commits = 0

        def execute(self, statement):
            if self.due:
                return _Result(items=content_ids)
            assert _statement_entity(statement) is NotificationOutbox
            return _Result(items=[])

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.commits += 1

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    due_db = CycleDB(due=True)
    digest_db = CycleDB()
    sessions = [due_db, digest_db]

    def finish_async(awaitable):
        awaitable.close()
        return True

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: sessions.pop(0))
    monkeypatch.setattr(tasks, "require_dispatch", lambda *_args: None)
    monkeypatch.setattr(
        tasks.arrow,
        "now",
        lambda *_args, **_kwargs: arrow.get(cycle_date, tzinfo="Asia/Seoul"),
    )
    monkeypatch.setattr(tasks, "_auto_publish_one", lambda content_id: outcomes[content_id])
    monkeypatch.setattr(tasks, "_run_async", finish_async)
    monkeypatch.setattr(tasks, "_enqueue_overdue_post_publish_review_notifications", lambda _now: 0)

    tasks.morning_content_auto_publish.run()

    outbox = [value for value in digest_db.added if isinstance(value, NotificationOutbox)]
    assert len(outbox) == 1
    assert outbox[0].notification_type == "CONTENT_PUBLISH_DIGEST"
    assert outbox[0].dedupe_key == f"CONTENT_PUBLISH_DIGEST:{cycle_date.isoformat()}"
    visible = str(outbox[0].payload)
    assert "병원 2곳 · 글 3건" in visible
    assert "사실 확인해주세요" in visible
    assert not any(row.notification_type == "CONTENT_PUBLISHED" for row in outbox)
    assert digest_db.commits == 1


def test_auto_publish_one_commits_publication_before_external_effects(monkeypatch):
    content_id = uuid.uuid4()
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        aeo_domain="test.example.com",
        treatments=[],
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )
    item = SimpleNamespace(
        id=content_id,
        hospital_id=hospital.id,
        hospital=hospital,
        status=tasks.ContentStatus.DRAFT,
        title="진료 전 확인할 점",
        body="상태에 따라 진료 방향을 설명합니다.",
        sequence_no=1,
        total_count=8,
        content_type=SimpleNamespace(value="FAQ"),
        scheduled_date=date(2026, 6, 10),
        carried_over_from=None,
        content_philosophy_id=None,
        essence_status=None,
        essence_check_summary=None,
        published_at=None,
        published_by=None,
        post_publish_notified_at=None,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
    )

    class DB:
        commits = 0
        execute_calls = 0

        def __init__(self):
            self.added = []

        def execute(self, _stmt):
            results = [_Result(items=[item]), _Result(items=[hospital])]
            result = results[self.execute_calls]
            self.execute_calls += 1
            return result

        def commit(self):
            self.commits += 1

        def add(self, value):
            self.added.append(value)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    db = DB()
    philosophy = SimpleNamespace(id=uuid.uuid4())
    assessment = SimpleNamespace(
        publishable=True,
        code=None,
        message=None,
        violations=(),
        essence_status="ALIGNED",
        essence_summary={"blocking": False},
        philosophy_id=philosophy.id,
    )
    audits = []
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(tasks, "assess_content_publication", lambda *_args: assessment)
    monkeypatch.setattr(
        tasks, "write_audit_log_sync", lambda *_args, **kwargs: audits.append(kwargs)
    )

    payload = tasks._auto_publish_one(content_id)

    assert db.commits == 1
    assert item.status == tasks.ContentStatus.PUBLISHED
    assert item.published_by == tasks.AUTO_PUBLISH_ACTOR
    assert item.published_at is not None
    assert payload["public_url"] == f"https://test.example.com/contents/{content_id}"
    assert audits[0]["action"] == "auto_publish_content"
    outbox = [value for value in db.added if isinstance(value, NotificationOutbox)]
    assert outbox == []
    assert item.post_publish_notified_at is None


# ── 08:00 자동 발행 안전 게이트: **실제** assess_content_publication으로 검증 ──
#
# 왜 monkeypatch를 쓰지 않는가: 게이트를 상수 assessment로 대체하면 차단 분기가 한 줄도
# 실행되지 않아, 게이트를 통째로 삭제해도 테스트가 초록이 된다. 제품 원칙상
# (docs/prd/REPUTATION-VERSIONUP-DIRECTIVE-2026-07.md §5-1-6) 자동검사 전항목 통과 건은
# AE가 본문을 열지 않고 종결하는 것이 정상 경로이므로, 이 게이트가 유일한 방어선이다.
# 따라서 아래 테스트들은 tasks._auto_publish_one → 실제 assess_content_publication →
# 실제 금지표현/운영기준 검사까지 전 구간을 통과시킨다.


def _statement_entity(stmt):
    descriptions = getattr(stmt, "column_descriptions", None) or []
    return descriptions[0].get("entity") if descriptions else None


class _AutoPublishDB:
    """statement의 **대상 엔티티**로 분기하는 fake 세션.

    기존 fake들은 execute 호출 순서(인덱스)로 결과를 돌려주는데, _auto_publish_one에
    조회가 하나만 추가돼도 인덱스가 밀려 엉뚱한 객체가 반환되고 테스트가 조용히
    의미를 잃는다. 엔티티로 분기하면 조회 순서·개수 변경에 영향을 받지 않는다.
    """

    def __init__(self, item, hospital):
        self.item = item
        self.hospital = hospital
        self.commits = 0
        self.added = []

    def execute(self, stmt):
        entity = _statement_entity(stmt)
        if entity is ContentItem:
            return _Result(items=[self.item] if self.item else [])
        if entity is Hospital:
            return _Result(items=[self.hospital] if self.hospital else [])
        if entity is OperationRun:
            return _Result(items=[])
        raise AssertionError(f"예상하지 못한 조회 대상: {entity}")

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _publication_hospital():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        aeo_domain="test.example.com",
        treatments=[],
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )


def _publication_item(hospital, *, body, title="진료 전 확인할 점"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        hospital=hospital,
        status=tasks.ContentStatus.DRAFT,
        title=title,
        body=body,
        image_url="https://storage.googleapis.com/reputation/content.png",
        meta_description="진료 전 확인할 점을 정리했습니다.",
        faq_question=None,
        faq_answer_summary=None,
        # 참고 자료 게이트(MISSING_REFERENCES)는 이 테스트들의 관심사가 아니므로
        # 화이트리스트 도메인의 실제 문서 URL로 미리 통과시켜 둔다.
        references_list=[
            {
                "title": "질병관리청 국가건강정보포털",
                "url": "https://health.kdca.go.kr/healthinfo/example",
            }
        ],
        sequence_no=1,
        total_count=8,
        content_type=SimpleNamespace(value="FAQ"),
        scheduled_date=date(2026, 6, 10),
        carried_over_from=None,
        content_philosophy_id=None,
        essence_status=None,
        essence_check_summary=None,
        published_at=None,
        published_by=None,
        post_publish_notified_at=None,
        post_publish_reviewed_at=None,
        post_publish_reviewed_by=None,
    )


def _approved_philosophy():
    """무결성 검사(오류 페이지 잔재)를 통과하는 최소 승인 운영 기준."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        version=3,
        status=PhilosophyStatus.APPROVED,
        avoid_messages=[],
    )


def _arm_external_effect_tripwires(monkeypatch) -> dict[str, list]:
    """차단 판정 뒤 공개 표면·색인·Slack으로 새는 효과가 있는지 감시한다.

    지금은 _auto_publish_one이 이 함수들을 직접 부르지 않지만, 커밋 이전으로 외부
    효과가 옮겨오는 리팩터가 들어오면 차단된 글이 공개될 수 있다. 그물을 미리 친다.
    """
    calls: dict[str, list] = {"revalidate": [], "indexnow": [], "published_slack": []}

    async def fake_revalidate(*args, **kwargs):
        calls["revalidate"].append((args, kwargs))
        return True

    async def fake_indexnow(*args, **kwargs):
        calls["indexnow"].append((args, kwargs))
        return True

    async def fake_published_slack(**kwargs):
        calls["published_slack"].append(kwargs)
        return True

    monkeypatch.setattr(tasks, "trigger_content_site_revalidate_safe", fake_revalidate)
    monkeypatch.setattr(tasks.indexnow, "submit_content_published_safe", fake_indexnow)
    monkeypatch.setattr(tasks.notifier, "notify_content_auto_published", fake_published_slack)
    return calls


def test_auto_publish_blocks_content_with_forbidden_expression(monkeypatch):
    """의료광고 금지 표현이 본문에 남아 있으면 08:00 자동 발행이 차단돼야 한다.

    승인된 운영 기준은 정상 제공한다 — 차단 사유가 '운영 기준 없음'이 아니라
    '금지 표현'임을 code로 못 박아, 금지표현 분기가 실제로 실행됐음을 증명한다.
    """
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="이 수술은 완치를 약속드립니다.")
    db = _AutoPublishDB(item, hospital)
    effects = _arm_external_effect_tripwires(monkeypatch)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: _approved_philosophy()
    )

    payload = tasks._auto_publish_one(item.id)

    assert payload["kind"] == "blocked"
    assert payload["code"] == "FORBIDDEN_EXPRESSION"
    assert item.status is tasks.ContentStatus.DRAFT
    assert item.published_at is None
    assert item.published_by is None
    assert [log.action for log in db.added if hasattr(log, "action")] == ["auto_publish_blocked"]
    runs = [value for value in db.added if isinstance(value, OperationRun)]
    assert len(runs) == 1
    assert runs[0].operation_type == "REGENERATE_CONTENT"
    assert runs[0].request_payload["_dispatch"]["target_id"] == str(item.id)
    assert effects == {"revalidate": [], "indexnow": [], "published_slack": []}


def test_auto_publish_blocks_markdown_hidden_forbidden_expression(monkeypatch):
    """마크다운 강조로 쪼갠 금지 표현(`최**고**의`)도 차단돼야 한다 — 회귀 방지선.

    공개 표면이 ReactMarkdown으로 렌더하므로 환자 화면에는 "최고의"로 보인다.
    원문 기준(check_forbidden)으로만 검사하면 이 우회가 그대로 통과해 공개된다.
    """
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="저희는 최**고**의 진료를 제공합니다.")
    db = _AutoPublishDB(item, hospital)
    effects = _arm_external_effect_tripwires(monkeypatch)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: _approved_philosophy()
    )

    payload = tasks._auto_publish_one(item.id)

    assert payload["kind"] == "blocked"
    assert payload["code"] == "FORBIDDEN_EXPRESSION"
    assert item.status is tasks.ContentStatus.DRAFT
    assert item.published_at is None
    assert item.published_by is None
    assert [log.action for log in db.added if hasattr(log, "action")] == ["auto_publish_blocked"]
    assert effects == {"revalidate": [], "indexnow": [], "published_slack": []}


def test_auto_publish_blocks_when_no_approved_philosophy(monkeypatch):
    """승인된 콘텐츠 운영 기준이 없으면 본문이 깨끗해도 발행하지 않는다 (STEP5 게이트)."""
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="증상 단계에 따라 진료 방향을 설명드립니다.")
    db = _AutoPublishDB(item, hospital)
    effects = _arm_external_effect_tripwires(monkeypatch)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: None)

    payload = tasks._auto_publish_one(item.id)

    assert payload["kind"] == "blocked"
    assert payload["code"] == "ESSENCE_NOT_ALIGNED"
    assert payload["reason"] == item.essence_check_summary["findings"][0]
    assert item.status is tasks.ContentStatus.DRAFT
    assert item.published_at is None
    assert item.published_by is None
    # 차단 사유가 DB에도 남아야 Admin에서 AE가 원인을 볼 수 있다.
    assert item.essence_status == tasks.ESSENCE_STATUS_MISSING_APPROVED
    assert item.essence_check_summary["blocking"] is True
    assert [log.action for log in db.added if hasattr(log, "action")] == ["auto_publish_blocked"]
    assert effects == {"revalidate": [], "indexnow": [], "published_slack": []}


def test_auto_publish_records_content_block_before_revalidation_dependency(monkeypatch):
    hospital = _publication_hospital()
    item = _publication_item(hospital, body=None, title=None)
    db = _AutoPublishDB(item, hospital)

    def unexpected_revalidation_check() -> None:
        raise AssertionError("blocked content needs no revalidation")

    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: None)
    monkeypatch.setattr(
        tasks,
        "ensure_site_revalidate_configured",
        unexpected_revalidation_check,
    )

    payload = tasks._auto_publish_one(item.id)

    assert payload["kind"] == "blocked"
    assert payload["code"] == "CONTENT_NOT_GENERATED"
    assert any(isinstance(value, OperationRun) for value in db.added)


def test_auto_publish_is_idempotent(monkeypatch):
    """같은 콘텐츠를 두 번 처리해도 발행·커밋·감사 기록은 각각 1회여야 한다.

    catch-up 윈도우 재실행이나 Celery 재시도로 morning 태스크가 같은 id를 다시 집는 일은
    정상이다. DRAFT 가드가 없으면 published_at이 덮어써지고 중복 후처리가 실행된다.
    """
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="증상 단계에 따라 진료 방향을 설명드립니다.")
    db = _AutoPublishDB(item, hospital)
    effects = _arm_external_effect_tripwires(monkeypatch)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: _approved_philosophy()
    )

    first = tasks._auto_publish_one(item.id)
    published_at = item.published_at
    second = tasks._auto_publish_one(item.id)

    assert first["kind"] == "published"
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert item.published_by == tasks.AUTO_PUBLISH_ACTOR
    # 두 번째 호출은 DRAFT 가드에 걸려 아무것도 하지 않는다.
    assert second is None
    assert db.commits == 1
    assert item.published_at == published_at
    audits = [log for log in db.added if hasattr(log, "action")]
    assert [log.action for log in audits] == ["auto_publish_content"]
    outbox = [value for value in db.added if isinstance(value, NotificationOutbox)]
    assert outbox == []
    assert effects["published_slack"] == []


def test_auto_publish_accepts_ready_status(monkeypatch):
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="증상 단계에 따라 진료 방향을 설명드립니다.")
    item.status = tasks.ContentStatus.READY
    db = _AutoPublishDB(item, hospital)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks, "get_current_approved_philosophy_sync", lambda *_args: _approved_philosophy()
    )

    payload = tasks._auto_publish_one(item.id)

    assert payload["kind"] == "published"
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert not any(isinstance(value, NotificationOutbox) for value in db.added)


def test_regeneration_discards_its_result_when_the_slot_was_cancelled(monkeypatch):
    """재생성이 도는 동안 AE가 슬롯을 종료하면 생성물은 버려져야 한다.

    가드가 없으면 종료된 슬롯에 미검수 AI 본문이 들어가고, 그 항목이 다시 DRAFT가 되면
    08:00 자동 발행(`status=DRAFT AND body IS NOT NULL`)이 이를 환자에게 공개한다.
    """
    item = SimpleNamespace(
        id="content-1",
        hospital_id="hospital-1",
        content_type=SimpleNamespace(value="FAQ"),
        title=None,
        body=None,
        image_url="gs://bucket/existing.png",
        published_at=None,
        published_by=None,
        brief_status=None,
        content_brief=None,
        query_target_id=None,
        exposure_action_id=None,
    )
    hospital = SimpleNamespace(id="hospital-1", slug="test-clinic")
    philosophy = SimpleNamespace(id="philosophy-1")

    class _ExistingTitles:
        def all(self):
            return []

    class _ApprovedPhilosophy:
        def scalar_one_or_none(self):
            return philosophy

    class _NoRowsWritten:
        rowcount = 0  # 운영자가 상태를 바꿔 조건부 UPDATE가 한 행도 못 잡은 상황

    class _CancelledDuringGenerationDB:
        def __init__(self):
            self._results = [_ExistingTitles(), _ApprovedPhilosophy()]
            self.commit_calls = 0
            self.rollback_calls = 0

        def execute(self, stmt):
            if isinstance(stmt, Update):
                return _NoRowsWritten()
            return self._results.pop(0)

        def commit(self):
            self.commit_calls += 1

        def rollback(self):
            self.rollback_calls += 1

        def refresh(self, _obj):
            pass

    async def fake_generate_content(*_args, **_kwargs):
        return {
            "title": "되살아나면 안 되는 제목",
            "body": "되살아나면 안 되는 본문",
            "meta_description": None,
            "references": [{"title": "질병관리청", "url": "https://www.kdca.go.kr/example"}],
            "faq_question": None,
            "faq_answer_summary": None,
        }

    def _boom_image(*_args, **_kwargs):
        raise AssertionError("결과를 버렸는데 이미지 생성까지 진행했다")

    monkeypatch.setattr(tasks, "generate_content", fake_generate_content)
    monkeypatch.setattr(tasks, "generate_image", _boom_image)
    monkeypatch.setattr(tasks, "get_current_approved_philosophy_sync", lambda *_args: philosophy)
    # 이 테스트의 대상은 write-back 가드다 — 브리프 플래너는 범위 밖이라 고정한다.
    monkeypatch.setattr(tasks, "prepare_automatic_content_brief_sync", lambda *a, **k: None)
    monkeypatch.setattr(
        tasks,
        "screen_content_against_philosophy",
        lambda _item, _philosophy: SimpleNamespace(status="ALIGNED", summary={"ok": True}),
    )

    db = _CancelledDuringGenerationDB()
    tasks._generate_single_content_item(db, item, hospital)

    assert db.rollback_calls == 1, "0행이면 롤백하고 결과를 버려야 한다"
    assert db.commit_calls == 1, "플래너 확정 커밋 외에 본문 커밋이 일어나면 안 된다"
    # 추적 객체가 오염되지 않아야 다음 반복이 안전하다.
    assert item.title is None
    assert item.body is None
