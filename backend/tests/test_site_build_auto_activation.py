"""기본 플랫폼 주소 자동 활성화 (STEP 4 → STEP 5).

게이트 세 가지는 모두 시스템 플래그다. 자기 도메인이 없는 병원은 허브 준비가 끝나는
즉시 운영이 시작되고, 자기 도메인이 지정된 병원과 일시 정지 병원만 손대지 않는다.
`build_aeo_site`는 acks_late·자율 복구로 두 번 이상 돌 수 있으므로 재실행에서
감사 로그도 알림도 늘지 않아야 한다.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.audit import AdminAuditLog
from app.models.hospital import Hospital, HospitalStatus
from app.services.hospital_activation import (
    AUTO_ACTIVATE_ACTOR,
    ActivationOutcome,
    AutoActivationBlocker,
    evaluate_auto_activation,
    public_site_url,
)
from app.services.onboarding_notifications import (
    ACTIVATED_NOTIFICATION_TYPE,
    SITE_BUILT_NOTIFICATION_TYPE,
)
from app.workers import tasks


class _FakeSyncDB:
    """`build_aeo_site`가 쓰는 만큼만 흉내내는 동기 세션."""

    def __init__(self, hospital: Hospital) -> None:
        self.hospital = hospital
        self.added: list[object] = []
        self.commits = 0
        self.executed: list[object] = []

    def __enter__(self) -> _FakeSyncDB:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def get(self, _model: object, object_id: uuid.UUID) -> Hospital | None:
        return self.hospital if self.hospital.id == object_id else None

    def execute(self, statement: object) -> _FakeResult:
        self.executed.append(statement)
        return _FakeResult()

    def add(self, item: object) -> None:
        self.added.append(item)

    def commit(self) -> None:
        self.commits += 1

    @property
    def audit_actions(self) -> list[str]:
        return [item.action for item in self.added if isinstance(item, AdminAuditLog)]


class _FakeResult:
    def scalar_one_or_none(self) -> None:
        return None

    def scalar_one(self) -> None:
        return None


def _hospital(
    *,
    status: HospitalStatus = HospitalStatus.BUILDING,
    site_built: bool = False,
    site_live: bool = False,
    aeo_domain: str | None = None,
    profile_complete: bool = True,
    v0_report_done: bool = True,
) -> Hospital:
    return Hospital(
        id=uuid.uuid4(),
        name="자동활성화의원",
        slug="auto-activate-clinic",
        status=status,
        aeo_domain=aeo_domain,
        profile_complete=profile_complete,
        v0_report_done=v0_report_done,
        site_built=site_built,
        site_live=site_live,
        treatments=[],
    )


def _run_build(monkeypatch, hospital: Hospital) -> tuple[_FakeSyncDB, list, list]:
    db = _FakeSyncDB(hospital)
    intents: list = []
    revalidated: list = []

    monkeypatch.setattr(tasks, "require_dispatch", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(
        tasks,
        "enqueue_onboarding_notification_sync",
        lambda _db, intent: intents.append(intent),
    )

    async def _noop_revalidate(slug, treatments=None, *, hospital_name=None):
        revalidated.append((slug, hospital_name))
        return True

    monkeypatch.setattr(tasks, "trigger_hospital_site_revalidate_safe", _noop_revalidate)
    tasks.build_aeo_site.run(str(hospital.id))
    return db, intents, revalidated


# ── 판정 (세션 없이) ────────────────────────────────────────────────


def test_platform_address_hospital_with_all_gates_is_auto_activatable() -> None:
    hospital = _hospital(site_built=True, status=HospitalStatus.PENDING_DOMAIN)
    assert evaluate_auto_activation(hospital) is None


def test_custom_domain_hospital_is_never_auto_activated() -> None:
    hospital = _hospital(site_built=True, aeo_domain="ai.clinic.co.kr")
    assert evaluate_auto_activation(hospital) is AutoActivationBlocker.CUSTOM_DOMAIN_PENDING


def test_paused_hospital_is_never_auto_activated() -> None:
    hospital = _hospital(site_built=True, status=HospitalStatus.PAUSED)
    assert (
        evaluate_auto_activation(hospital)
        is AutoActivationBlocker.STATUS_NOT_AUTO_ADVANCEABLE
    )


def test_missing_gate_blocks_auto_activation() -> None:
    hospital = _hospital(site_built=True, profile_complete=False)
    assert evaluate_auto_activation(hospital) is AutoActivationBlocker.GATES_NOT_MET


def test_active_hospital_reports_already_active() -> None:
    hospital = _hospital(site_built=True, site_live=True, status=HospitalStatus.ACTIVE)
    assert evaluate_auto_activation(hospital) is AutoActivationBlocker.ALREADY_ACTIVE


# ── 워커 경로 ───────────────────────────────────────────────────────


def test_site_build_auto_activates_platform_address(monkeypatch) -> None:
    hospital = _hospital()
    db, intents, revalidated = _run_build(monkeypatch, hospital)

    assert hospital.site_built is True
    assert hospital.status is HospitalStatus.ACTIVE
    assert hospital.site_live is True
    assert db.audit_actions == ["activate_hospital"]
    audit = next(item for item in db.added if isinstance(item, AdminAuditLog))
    assert audit.actor == AUTO_ACTIVATE_ACTOR
    assert audit.detail["activation_method"] == "platform_subdomain"
    assert audit.detail["reason"] == "SITE_BUILD_AUTO_ACTIVATION"
    assert [intent.notification_type for intent in intents] == [ACTIVATED_NOTIFICATION_TYPE]
    assert "운영 시작됨" in intents[0].message.fallback_text
    assert public_site_url(None, hospital.slug) in intents[0].message.fallback_text
    assert revalidated == [(hospital.slug, hospital.name)]
    assert db.commits == 1


def test_custom_domain_build_keeps_the_manual_nag_and_says_why(monkeypatch) -> None:
    hospital = _hospital(aeo_domain="ai.clinic.co.kr")
    db, intents, revalidated = _run_build(monkeypatch, hospital)

    assert hospital.site_built is True
    assert hospital.status is HospitalStatus.PENDING_DOMAIN
    assert hospital.site_live is False
    assert db.audit_actions == []
    assert [intent.notification_type for intent in intents] == [SITE_BUILT_NOTIFICATION_TYPE]
    assert "자기 도메인" in intents[0].message.fallback_text
    assert revalidated == []


def test_paused_hospital_build_is_not_reactivated(monkeypatch) -> None:
    hospital = _hospital(status=HospitalStatus.PAUSED)
    db, intents, revalidated = _run_build(monkeypatch, hospital)

    assert hospital.site_built is True
    assert hospital.status is HospitalStatus.PAUSED
    assert hospital.site_live is False
    assert db.audit_actions == []
    assert [intent.notification_type for intent in intents] == [SITE_BUILT_NOTIFICATION_TYPE]
    assert revalidated == []


def test_second_build_run_adds_no_audit_row_and_no_notification(monkeypatch) -> None:
    hospital = _hospital()
    _run_build(monkeypatch, hospital)
    second_db, second_intents, second_revalidated = _run_build(monkeypatch, hospital)

    assert hospital.status is HospitalStatus.ACTIVE
    assert second_db.audit_actions == []
    assert second_intents == []
    assert second_revalidated == []
    assert second_db.commits == 0


def test_already_built_but_unactivated_hospital_recovers_on_rerun(monkeypatch) -> None:
    """허브는 준비됐는데 활성화가 안 된 병원은 재실행이 되살린다 (SITE_BUILT 재알림 없이)."""

    hospital = _hospital(site_built=True, status=HospitalStatus.PENDING_DOMAIN)
    db, intents, revalidated = _run_build(monkeypatch, hospital)

    assert hospital.status is HospitalStatus.ACTIVE
    assert hospital.site_live is True
    assert [intent.notification_type for intent in intents] == [ACTIVATED_NOTIFICATION_TYPE]
    assert db.commits == 1
    assert revalidated == [(hospital.slug, hospital.name)]


def test_build_before_profile_and_v0_gates_changes_nothing(monkeypatch) -> None:
    hospital = _hospital(profile_complete=False)
    db, intents, revalidated = _run_build(monkeypatch, hospital)

    assert hospital.site_built is False
    assert hospital.status is HospitalStatus.BUILDING
    assert db.commits == 0
    assert intents == []
    assert revalidated == []


# ── 엔드포인트가 같은 서비스를 쓰는지 ───────────────────────────────


@pytest.mark.asyncio
async def test_activate_endpoint_uses_the_shared_transition() -> None:
    from app.api.admin.hospitals import activate_hospital as activate_endpoint
    from app.services import hospital_activation

    hospital = _hospital(site_built=True, status=HospitalStatus.PENDING_DOMAIN)

    class _FakeAsyncDB:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.commits = 0

        async def get(self, _model: object, object_id: uuid.UUID) -> Hospital | None:
            return hospital if hospital.id == object_id else None

        async def scalar(self, _statement: object) -> None:
            return None

        def add(self, item: object) -> None:
            self.added.append(item)

        async def commit(self) -> None:
            self.commits += 1

    db = _FakeAsyncDB()
    calls: list[str] = []
    original = hospital_activation.activate_hospital

    async def _tracked(*args, **kwargs):
        calls.append(kwargs["reason"])
        return await original(*args, **kwargs)

    import app.api.admin.hospitals as hospitals_api

    hospitals_api.activate_hospital_transition = _tracked  # type: ignore[assignment]
    hospitals_api.ensure_site_revalidate_configured = lambda: None  # type: ignore[assignment]

    async def _noop_revalidate(*_args, **_kwargs):
        return True

    hospitals_api.trigger_hospital_site_revalidate_safe = _noop_revalidate  # type: ignore[assignment]
    try:
        result = await activate_endpoint(hospital.id, db)
    finally:
        hospitals_api.activate_hospital_transition = original  # type: ignore[assignment]

    assert calls == ["OPERATOR_PLATFORM_ACTIVATION"]
    assert result["site_live"] is True
    assert hospital.status is HospitalStatus.ACTIVE
    audit = next(item for item in db.added if isinstance(item, AdminAuditLog))
    assert audit.action == "activate_hospital"
    assert audit.actor != AUTO_ACTIVATE_ACTOR
    assert db.commits == 1


@pytest.mark.asyncio
async def test_shared_transition_is_idempotent_for_an_active_hospital() -> None:
    from app.services.hospital_activation import activate_hospital

    hospital = _hospital(site_built=True, site_live=True, status=HospitalStatus.ACTIVE)

    class _NoWriteDB:
        def add(self, _item: object) -> None:  # pragma: no cover - must not be reached
            raise AssertionError("already-active activation must not write")

        async def scalar(self, _statement: object) -> None:  # pragma: no cover
            raise AssertionError("already-active activation must not lock")

    outcome = await activate_hospital(_NoWriteDB(), hospital, actor="AE", reason="RETRY")
    assert outcome.outcome is ActivationOutcome.ALREADY_ACTIVE
    assert outcome.activated_now is False


# ── 마일스톤: 자동 활성화 대상은 재촉하지 않는다 ────────────────────


def _handoff(hospital: Hospital):
    from datetime import UTC, datetime

    from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff

    moment = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    hospital.updated_at = moment
    return HospitalHandoff(
        id=uuid.uuid4(),
        hospital_id=hospital.id,
        state=HandoffState.HANDOFF_ACCEPTED,
        accepted_at=moment,
        acceptance_source=HandoffSource.DIRECT_CREATE,
        created_at=moment,
        updated_at=moment,
    )


def _milestone_kind(hospital: Hospital) -> str | None:
    from datetime import UTC, datetime

    from app.workers.milestone_onboarding_projection import _current_event

    event = _current_event(hospital, _handoff(hospital), None, datetime(2026, 9, 2, tzinfo=UTC))
    return None if event is None else event.event_type.value


def test_activation_ready_is_not_projected_for_the_auto_activation_path() -> None:
    hospital = _hospital(site_built=True, status=HospitalStatus.PENDING_DOMAIN)
    assert _milestone_kind(hospital) == "HANDOFF_ACCEPTED"


def test_activation_ready_is_not_projected_for_an_active_hospital() -> None:
    hospital = _hospital(site_built=True, site_live=True, status=HospitalStatus.ACTIVE)
    assert _milestone_kind(hospital) == "HOSPITAL_ACTIVE"


def test_activation_ready_is_not_projected_for_a_paused_hospital() -> None:
    hospital = _hospital(site_built=True, status=HospitalStatus.PAUSED)
    assert _milestone_kind(hospital) == "HANDOFF_ACCEPTED"


def test_activation_ready_still_fires_for_a_custom_domain_hospital() -> None:
    hospital = _hospital(site_built=True, aeo_domain="ai.clinic.co.kr")
    assert _milestone_kind(hospital) == "ACTIVATION_READY"


# ── 자율 복구: 자동 활성화가 유실된 병원도 되찾는다 ─────────────────


def test_reconciler_also_selects_built_but_unactivated_platform_hospitals(monkeypatch) -> None:
    """STEP5 재촉 Slack을 없앤 뒤에는 이 재배달이 유일한 복구 경로다."""

    from sqlalchemy.dialects import postgresql

    from app.workers import autonomous_recovery

    captured: list[object] = []

    class _EmptyResult:
        def scalars(self) -> _EmptyResult:
            return self

        def all(self) -> list[object]:
            return []

    class _CapturingSession:
        def execute(self, statement: object) -> _EmptyResult:
            captured.append(statement)
            return _EmptyResult()

        def commit(self) -> None:
            return None

        def __enter__(self) -> _CapturingSession:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    monkeypatch.setattr(autonomous_recovery, "SyncSessionLocal", _CapturingSession)
    monkeypatch.setattr(autonomous_recovery, "require_dispatch", lambda *_a, **_k: None)
    autonomous_recovery.reconcile.run()

    hospital_statement = next(
        item for item in captured if item.column_descriptions[0].get("entity") is Hospital
    )
    compiled = str(
        hospital_statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "hospitals.site_built IS false" in compiled
    assert "hospitals.site_live IS false" in compiled
    assert "PENDING_DOMAIN" in compiled
    assert "hospitals.aeo_domain IS NULL" in compiled
