"""#6/#9/#11 — create_hospital 감사 로그 + 경합 409, pause/resume 라이프사이클."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.admin import hospitals as hospitals_api
from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import DomainDnsStrategy, Hospital, HospitalStatus, Plan
from app.models.monthly_control import HospitalServiceInterval


# ── create_hospital ──────────────────────────────────────────────
class _CreateDB:
    def __init__(self, existing=None, fail_commit=False):
        self.existing = existing
        self.fail_commit = fail_commit
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt):
        return SimpleNamespace(
            scalar_one_or_none=lambda: self.existing,
            scalars=lambda: SimpleNamespace(
                all=lambda: [self.existing] if self.existing is not None else []
            ),
        )

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        pass

    async def commit(self):
        if self.fail_commit:
            raise IntegrityError("INSERT", {}, Exception("duplicate key value"))
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, item):
        pass


async def test_create_hospital_writes_audit_log():
    db = _CreateDB()
    body = hospitals_api.HospitalCreate(name="장편한외과의원", plan=Plan.PLAN_12)

    response = await hospitals_api.create_hospital(body, db=db)

    assert response["name"] == "장편한외과의원"
    assert db.committed is True
    audit_rows = [a for a in db.added if getattr(a, "action", None) == "create_hospital"]
    assert len(audit_rows) == 1
    assert audit_rows[0].detail["plan"] == "PLAN_12"


async def test_create_hospital_rejects_exact_duplicate_name():
    existing = Hospital(
        id=uuid.uuid4(),
        name="행복드림의원",
        slug="happy-dream",
        status=HospitalStatus.ACTIVE,
    )
    db = _CreateDB(existing=existing)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.create_hospital(
            hospitals_api.HospitalCreate(name="  행복드림의원  "),
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DUPLICATE_HOSPITAL_NAME"
    assert exc.value.detail["candidates"][0]["id"] == str(existing.id)


async def test_create_hospital_converts_race_integrity_error_to_409():
    db = _CreateDB(fail_commit=True)
    body = hospitals_api.HospitalCreate(name="장편한외과의원")

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.create_hospital(body, db=db)

    assert exc.value.status_code == 409
    assert "슬러그 또는 도메인" in exc.value.detail
    assert db.rolled_back is True
    assert db.committed is False


class _IdempotentCreateDB(_CreateDB):
    async def get(self, model, object_id):
        return next(
            (
                item
                for item in self.added
                if isinstance(item, model) and getattr(item, "id", None) == object_id
            ),
            None,
        )

    async def execute(self, stmt):
        if "hospital_handoffs" in str(stmt):
            handoff = next(
                (item for item in self.added if isinstance(item, HospitalHandoff)),
                None,
            )
            return SimpleNamespace(
                scalar_one_or_none=lambda: handoff,
                scalars=lambda: SimpleNamespace(all=lambda: []),
            )
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )

    async def flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()


async def test_create_hospital_replays_same_onboarding_request_without_duplicate():
    db = _IdempotentCreateDB()
    request_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()
    body = hospitals_api.HospitalCreate(
        name="재시도 의원",
        plan=Plan.PLAN_16,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        onboarding_request_id=request_id,
    )

    first = await hospitals_api.create_hospital(body, db=db)
    second = await hospitals_api.create_hospital(body, db=db)

    hospitals = [item for item in db.added if item.__class__.__name__ == "Hospital"]
    handoffs = [item for item in db.added if isinstance(item, HospitalHandoff)]
    assert len(hospitals) == 1
    assert len(handoffs) == 1
    assert first["id"] == second["id"] == str(request_id)
    assert first["handoff"]["id"] == second["handoff"]["id"]


async def test_create_hospital_replay_returns_contract_handoff_fields_for_resume():
    db = _IdempotentCreateDB()
    request_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()
    body = hospitals_api.HospitalCreate(
        name="재시도 의원",
        plan=Plan.PLAN_16,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        onboarding_request_id=request_id,
    )
    await hospitals_api.create_hospital(body, db=db)
    handoff = next(item for item in db.added if isinstance(item, HospitalHandoff))
    contracted_at = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    sla_due_at = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)
    handoff.state = HandoffState.CONTRACTED
    handoff.acceptance_source = HandoffSource.DIRECT_CREATE
    handoff.contract_reference = "CTR-DIRECT-1"
    handoff.contract_effective_at = contracted_at
    handoff.plan = Plan.PLAN_16
    handoff.sla_due_at = sla_due_at

    replay = await hospitals_api.create_hospital(body, db=db)

    assert replay["handoff"]["contract_reference"] == "CTR-DIRECT-1"
    assert replay["handoff"]["contract_effective_at"] == contracted_at
    assert replay["handoff"]["plan"] == Plan.PLAN_16
    assert replay["handoff"]["sla_due_at"] == sla_due_at


class _ConcurrentIdempotentCreateDB(_CreateDB):
    def __init__(self, *, prior_hospital, prior_handoff):
        super().__init__()
        self.prior_hospital = prior_hospital
        self.prior_handoff = prior_handoff

    async def get(self, model, object_id):
        if self.rolled_back and model is Hospital and object_id == self.prior_hospital.id:
            return self.prior_hospital
        return None

    async def execute(self, stmt):
        if self.rolled_back and "hospital_handoffs" in str(stmt):
            return SimpleNamespace(
                scalar_one_or_none=lambda: self.prior_handoff,
                scalars=lambda: SimpleNamespace(all=lambda: []),
            )
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )

    async def commit(self):
        raise IntegrityError("INSERT", {}, Exception("duplicate key value"))


async def test_create_hospital_recovers_concurrent_same_onboarding_request():
    request_id = uuid.uuid4()
    sales_owner_id = uuid.uuid4()
    ae_owner_id = uuid.uuid4()
    prior_hospital = Hospital(
        id=request_id,
        name="동시등록의원",
        slug="dongsideungroguiweon",
        plan=Plan.PLAN_20,
    )
    prior_handoff = HospitalHandoff.pending(
        request_id,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        source=HandoffSource.DIRECT_CREATE,
    )
    db = _ConcurrentIdempotentCreateDB(
        prior_hospital=prior_hospital,
        prior_handoff=prior_handoff,
    )
    body = hospitals_api.HospitalCreate(
        name="동시등록의원",
        plan=Plan.PLAN_20,
        sales_owner_id=sales_owner_id,
        ae_owner_id=ae_owner_id,
        onboarding_request_id=request_id,
    )

    response = await hospitals_api.create_hospital(body, db=db)

    assert db.rolled_back is True
    assert response["id"] == str(request_id)
    assert response["handoff"]["id"] == prior_handoff.id


async def test_create_hospital_rejects_concurrent_onboarding_request_payload_mismatch():
    request_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    prior_hospital = Hospital(
        id=request_id,
        name="먼저등록된의원",
        slug="meonjeodeungrogdoenuiweon",
        plan=Plan.PLAN_12,
    )
    prior_handoff = HospitalHandoff.pending(
        request_id,
        sales_owner_id=owner_id,
        ae_owner_id=owner_id,
        source=HandoffSource.DIRECT_CREATE,
    )
    db = _ConcurrentIdempotentCreateDB(
        prior_hospital=prior_hospital,
        prior_handoff=prior_handoff,
    )
    body = hospitals_api.HospitalCreate(
        name="다른의원",
        plan=Plan.PLAN_12,
        sales_owner_id=owner_id,
        ae_owner_id=owner_id,
        onboarding_request_id=request_id,
    )

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.create_hospital(body, db=db)

    assert db.rolled_back is True
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "ONBOARDING_REQUEST_CONFLICT"


# ── pause / resume ───────────────────────────────────────────────
class _LifecycleDB:
    def __init__(
        self,
        hospital,
        *,
        handoff_state=HandoffState.HANDOFF_ACCEPTED,
        interval=None,
        events=None,
        locked_domain=None,
        locked_strategy=None,
    ):
        self.hospital = hospital
        self.handoff_state = handoff_state
        self.interval = interval
        self.added = []
        self.committed = False
        #: 이 세션에서 실제로 일어난 순서(commit / revalidate). revalidate 스텁과 같은
        #: 리스트를 공유해야 커밋 이후 호출인지까지 확인할 수 있다.
        self.events = [] if events is None else events
        #: 잠금 재조회가 돌려줄 도메인·연결 방식. 둘 다 None이면 병원 행 그대로 —
        #: 경합 없는 정상 경로다.
        self.locked_domain = locked_domain
        self.locked_strategy = locked_strategy
        #: 잠금 아래서 실제로 읽어온 도메인들 — 재조회가 일어났는지 테스트가 확인한다.
        self.locked_reads = []

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    async def scalar(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        if entity is HospitalHandoff:
            return self.handoff_state
        if entity is HospitalServiceInterval:
            return self.interval
        if entity is Hospital and stmt.column_descriptions[0].get("expr") is Hospital:
            # resume 의 도메인 재확인(SELECT ... FOR UPDATE). 잠금 시점의 행을 돌려준다.
            # 구간 잠금이 쓰는 select(Hospital.id) 와 달리 행 전체를 고르는 문장만 해당한다.
            changed = {}
            if self.locked_domain is not None:
                changed["aeo_domain"] = self.locked_domain
            if self.locked_strategy is not None:
                changed["domain_dns_strategy"] = self.locked_strategy
            row = (
                self.hospital
                if not changed
                else SimpleNamespace(**{**vars(self.hospital), **changed})
            )
            self.locked_reads.append(row.aeo_domain)
            return row
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True
        self.events.append("commit")

    async def refresh(self, item):
        pass


def _full_hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status=HospitalStatus.ACTIVE,
        plan=Plan.PLAN_12,
        source_lead_id=None,
        onboarding_note=None,
        address="서울 성동구",
        phone="02-000-0000",
        business_hours=None,
        website_url=None,
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url=None,
        naver_place_url=None,
        aeo_domain=None,
        domain_cert_dns_verified_at=None,
        domain_last_checked_at=None,
        domain_last_check_ok=None,
        domain_last_check_reason=None,
        latitude=None,
        longitude=None,
        wikidata_qid=None,
        gbp_place_id=None,
        naver_place_id=None,
        kakao_place_id=None,
        hira_org_id=None,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        competitors=[],
        director_name="김원장",
        director_career=None,
        director_philosophy=None,
        director_credentials=None,
        treatments=[],
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _record_site_revalidate(monkeypatch):
    """공개 사이트 캐시 갱신 호출을 기록만 하고 네트워크는 타지 않는다.

    `events` 리스트를 `_LifecycleDB(hospital, events=...)`로 넘기면 commit 과 revalidate 가
    한 벌에 쌓여, 호출 순서까지 테스트가 확인할 수 있다.
    """
    recorder = SimpleNamespace(calls=[], events=[])

    async def _fake(slug, treatments=None, *, hospital_name=None):
        recorder.calls.append((slug, hospital_name))
        recorder.events.append("revalidate")
        return True

    monkeypatch.setattr(hospitals_api, "trigger_hospital_site_revalidate_safe", _fake)
    return recorder


@pytest.mark.parametrize("start_status", [HospitalStatus.ACTIVE, HospitalStatus.PENDING_DOMAIN])
async def test_pause_from_active_or_pending(start_status):
    hospital = _full_hospital(status=start_status)
    db = _LifecycleDB(hospital)

    response = await hospitals_api.pause_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.PAUSED
    assert response["status"] == HospitalStatus.PAUSED
    assert db.committed is True
    assert [a.action for a in db.added] == ["pause_hospital"]


@pytest.mark.parametrize(
    "start_status",
    [HospitalStatus.ONBOARDING, HospitalStatus.PAUSED, HospitalStatus.BUILDING],
)
async def test_pause_rejected_from_other_states(start_status):
    hospital = _full_hospital(status=start_status)
    db = _LifecycleDB(hospital)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.pause_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert hospital.status == start_status
    assert db.committed is False


async def test_resume_to_active_when_gates_and_site_live_met():
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=True)
    db = _LifecycleDB(hospital)

    response = await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert response["status"] == HospitalStatus.ACTIVE
    assert db.committed is True
    assert [a.action for a in db.added if hasattr(a, "action")] == ["resume_hospital"]


async def test_resume_allows_missing_schedule():
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=False, schedule_set=False)
    db = _LifecycleDB(hospital)

    response = await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert response["status"] == HospitalStatus.ACTIVE
    assert db.committed is True
    assert [a.action for a in db.added if hasattr(a, "action")] == ["resume_hospital"]


async def test_resume_rejected_when_not_paused():
    hospital = _full_hospital(status=HospitalStatus.ACTIVE)
    db = _LifecycleDB(hospital)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert db.committed is False


@pytest.mark.parametrize(
    ("missing_key", "overrides", "handoff_state"),
    [
        ("profile_complete", {"profile_complete": False}, HandoffState.HANDOFF_ACCEPTED),
        ("site_built", {"site_built": False}, HandoffState.HANDOFF_ACCEPTED),
    ],
)
async def test_resume_blocks_each_authoritative_gate_without_interval(
    missing_key, overrides, handoff_state
):
    hospital = _full_hospital(status=HospitalStatus.PAUSED, **overrides)
    db = _LifecycleDB(hospital, handoff_state=handoff_state)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["missing"] == [missing_key]
    assert hospital.status == HospitalStatus.PAUSED
    assert not any(isinstance(item, HospitalServiceInterval) for item in db.added)


async def test_resume_custom_domain_requires_current_dns(monkeypatch):
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        aeo_domain="clinic.example.com",
    )
    db = _LifecycleDB(hospital)

    async def _unverified_dns(domain, strategy):
        return SimpleNamespace(verified=False)

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _unverified_dns)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DOMAIN_NOT_READY"
    assert hospital.status == HospitalStatus.PAUSED
    assert db.added == []


async def test_resume_custom_domain_no_longer_requires_certificate(monkeypatch):
    """DM-F4: 운영 재개 시 DNS 검증만 확인, 인증서는 선행조건 아님."""
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        aeo_domain="clinic.example.com",
        site_live=True,  # DNS already verified
    )
    db = _LifecycleDB(hospital)

    async def _verified_dns(domain, strategy):
        return SimpleNamespace(verified=True)

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _verified_dns)
    # DM-F4: resume does not call ensure_verified_domain_certificate

    result = await hospitals_api.resume_hospital(hospital.id, db=db)

    assert result["status"] == "ACTIVE"
    assert hospital.status == HospitalStatus.ACTIVE
    # 관측을 남기기 전에 잠금 아래서 도메인을 다시 읽고, 같은 도메인임을 확인한다.
    assert db.locked_reads == ["clinic.example.com"]


async def test_pause_revalidates_public_site_after_commit(_record_site_revalidate):
    """H-06: 일시정지 뒤 공개 페이지가 최대 30분 더 보이면 안 된다."""
    hospital = _full_hospital(status=HospitalStatus.ACTIVE, site_live=True)
    db = _LifecycleDB(hospital, events=_record_site_revalidate.events)

    await hospitals_api.pause_hospital(hospital.id, db=db)

    assert db.committed is True
    assert _record_site_revalidate.calls == [(hospital.slug, hospital.name)]
    # 갱신은 반드시 커밋 뒤다 — 순서가 뒤집히면 커밋되지 않은 상태로 공개 캐시를 채운다.
    assert _record_site_revalidate.events == ["commit", "revalidate"]


async def test_resume_revalidates_public_site_after_commit(_record_site_revalidate):
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=True)
    db = _LifecycleDB(hospital, events=_record_site_revalidate.events)

    await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert _record_site_revalidate.calls == [(hospital.slug, hospital.name)]
    assert _record_site_revalidate.events == ["commit", "revalidate"]


async def test_resume_records_live_domain_evidence_for_custom_domain(monkeypatch):
    """M-11: 재개가 DNS를 확인했으면 배지가 읽는 관측 필드에도 남겨야 A-1이 재발하지 않는다."""
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        site_live=True,
        aeo_domain="clinic.example.com",
    )
    db = _LifecycleDB(hospital)

    async def _dns_ok(domain, strategy):
        return SimpleNamespace(verified=True)

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _dns_ok)

    await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert hospital.domain_last_checked_at is not None
    assert hospital.domain_last_check_reason == "dns_ok"
    # DNS 조회는 이름이 어디를 가리키는지만 보여줄 뿐 TLS·라우팅을 증명하지 못하므로
    # domain_last_check_ok 는 판단 보류(None)로 남는다(domain_live_status 규칙).
    # 배지를 '공개 주소 확인 대기'에서 벗어나게 하는 관측은 DNS 검증 시각 쪽이다.
    assert hospital.domain_last_check_ok is None
    assert hospital.domain_cert_dns_verified_at is not None


async def test_resume_refuses_when_domain_changed_during_dns_check(
    monkeypatch, _record_site_revalidate
):
    """확인한 도메인과 기록할 행의 도메인이 다르면 관측을 남기지 않고 409로 막는다.

    DNS 조회는 잠금 밖에서 일어난다. 그 사이 다른 요청이 도메인을 바꿨는데도 그대로
    기록하면, 한 번도 확인된 적 없는 새 주소가 '확인 완료'로 보인다.
    """
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        site_live=True,
        aeo_domain="clinic.example.com",
    )
    db = _LifecycleDB(
        hospital,
        events=_record_site_revalidate.events,
        locked_domain="other.example.com",
    )

    async def _dns_ok(domain, strategy):
        return SimpleNamespace(verified=True)

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _dns_ok)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DOMAIN_CHANGED"
    assert hospital.status == HospitalStatus.PAUSED
    assert hospital.domain_cert_dns_verified_at is None
    assert hospital.domain_last_checked_at is None
    assert hospital.domain_last_check_ok is None
    assert hospital.domain_last_check_reason is None
    assert db.committed is False
    assert db.added == []
    assert _record_site_revalidate.calls == []


async def test_resume_refuses_when_dns_strategy_changed_during_check(
    monkeypatch, _record_site_revalidate
):
    """도메인이 그대로여도 연결 방식이 바뀌었으면 관측을 남기지 않고 409로 막는다.

    CNAME으로 확인한 성공을 APEX_ADDRESS 행에 붙이면, 실제로는 확인된 적 없는
    레코드 설정이 '확인 완료'로 보인다 — 도메인 교체와 같은 종류의 거짓 근거다.
    """
    hospital = _full_hospital(
        status=HospitalStatus.PAUSED,
        site_live=True,
        aeo_domain="clinic.example.com",
        domain_dns_strategy=DomainDnsStrategy.CNAME,
    )
    db = _LifecycleDB(
        hospital,
        events=_record_site_revalidate.events,
        locked_strategy=DomainDnsStrategy.APEX_ADDRESS,
    )

    async def _dns_ok(domain, strategy):
        assert strategy is DomainDnsStrategy.CNAME
        return SimpleNamespace(verified=True)

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _dns_ok)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.resume_hospital(hospital.id, db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "DOMAIN_CHANGED"
    assert hospital.status == HospitalStatus.PAUSED
    assert hospital.domain_cert_dns_verified_at is None
    assert hospital.domain_last_checked_at is None
    assert hospital.domain_last_check_ok is None
    assert hospital.domain_last_check_reason is None
    assert db.committed is False
    assert db.added == []
    assert _record_site_revalidate.calls == []


async def test_pause_leaves_domain_observation_untouched():
    """일시정지는 도메인 사실을 바꾸지 않는다 — 관측을 지우면 재개 때 다시 확인해야 한다."""
    checked_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    hospital = _full_hospital(
        status=HospitalStatus.ACTIVE,
        aeo_domain="clinic.example.com",
        domain_cert_dns_verified_at=checked_at,
        domain_last_checked_at=checked_at,
        domain_last_check_ok=True,
        domain_last_check_reason="dns_ok",
    )
    db = _LifecycleDB(hospital)

    await hospitals_api.pause_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.PAUSED
    assert hospital.domain_cert_dns_verified_at == checked_at
    assert hospital.domain_last_checked_at == checked_at
    assert hospital.domain_last_check_ok is True
    assert hospital.domain_last_check_reason == "dns_ok"


async def test_resume_without_custom_domain_skips_dns_check(monkeypatch):
    """기본 플랫폼 주소만 쓰는 병원은 DNS 확인 대상이 아니다 — 조회도 관측도 없다."""
    hospital = _full_hospital(status=HospitalStatus.PAUSED, site_live=True, aeo_domain=None)
    db = _LifecycleDB(hospital)

    async def _must_not_be_called(domain, strategy):
        raise AssertionError("must not be called")

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _must_not_be_called)

    await hospitals_api.resume_hospital(hospital.id, db=db)

    assert hospital.status == HospitalStatus.ACTIVE
    assert db.locked_reads == []
    assert hospital.domain_cert_dns_verified_at is None
    assert hospital.domain_last_checked_at is None
    assert hospital.domain_last_check_ok is None
    assert hospital.domain_last_check_reason is None
