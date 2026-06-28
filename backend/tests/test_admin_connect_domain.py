"""PATCH /admin/hospitals/{id}/domain 입력 강화 —
정규화 저장, 형식/플랫폼 도메인 422(한국어), 타 병원 중복 409(한국어).
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import domain_connect as domain_connect_api
from app.api.admin import hospitals as hospitals_api
from app.models.hospital import DomainDnsStrategy, HospitalStatus


class _Scalars:
    def __init__(self, item):
        self._item = item

    def first(self):
        return self._item


class _Result:
    def __init__(self, item):
        self._item = item

    def scalars(self):
        return _Scalars(self._item)


class FakeDB:
    """get → 대상 병원, execute → 중복 도메인 조회 결과."""

    def __init__(self, hospital, duplicate_owner=None):
        self._hospital = hospital
        self._duplicate_owner = duplicate_owner
        self.added = []
        self.committed = False
        self.statements = []

    async def get(self, model, object_id):
        return self._hospital if object_id == self._hospital.id else None

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._duplicate_owner)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="장편한외과의원",
        slug="jang-clinic",
        status=HospitalStatus.PENDING_DOMAIN,
        plan=None,
        source_lead_id=None,
        onboarding_note=None,
        address=None,
        phone=None,
        business_hours=None,
        website_url=None,
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url=None,
        naver_place_url=None,
        site_live=False,
        site_built=False,
        aeo_domain=None,
        domain_dns_strategy=DomainDnsStrategy.CNAME,
        latitude=None,
        longitude=None,
        wikidata_qid=None,
        gbp_place_id=None,
        naver_place_id=None,
        kakao_place_id=None,
        hira_org_id=None,
        region=[],
        specialties=[],
        keywords=[],
        competitors=[],
        director_name=None,
        director_career=None,
        director_philosophy=None,
        director_credentials=None,
        treatments=[],
        profile_complete=False,
        v0_report_done=False,
        schedule_set=False,
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


async def _connect(db, hospital, raw_domain, **metadata):
    return await domain_connect_api.connect_domain(
        hospital.id,
        domain_connect_api.DomainConnect(domain=raw_domain, **metadata),
        BackgroundTasks(),
        db=db,
    )


async def test_connect_domain_saves_normalized_hostname():
    """스킴/경로/대소문자/끝 점이 섞여 들어와도 정규화된 호스트명으로 저장한다."""
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _connect(db, hospital, "https://Info.Jangpyeon.COM/path/.")

    assert hospital.aeo_domain == "info.jangpyeon.com"
    assert "info.jangpyeon.com" in result["detail"]
    assert db.committed is True


@pytest.mark.parametrize("raw", ["not a domain!!", "localhost", "clinic_example.com", "..."])
async def test_connect_domain_invalid_format_is_korean_422(raw):
    hospital = _hospital()
    db = FakeDB(hospital)

    with pytest.raises(HTTPException) as exc_info:
        await _connect(db, hospital, raw)

    assert exc_info.value.status_code == 422
    assert "유효한 도메인 형식이 아닙니다" in exc_info.value.detail
    assert hospital.aeo_domain is None
    assert db.committed is False


@pytest.mark.parametrize(
    "raw",
    [
        "cname.reputation.motionlabs.kr",
        "CNAME.Reputation.Motionlabs.KR",
        "clinic.cname.reputation.motionlabs.kr",
        "reputation.motionlabs.kr",
        "admin.reputation.motionlabs.kr",
    ],
)
async def test_connect_domain_rejects_platform_domain(monkeypatch, raw):
    monkeypatch.setattr(domain_connect_api.settings, "CNAME_TARGET", "cname.reputation.motionlabs.kr")
    monkeypatch.setattr(domain_connect_api.settings, "SITE_BASE_URL", "https://reputation.motionlabs.kr")
    monkeypatch.setattr(domain_connect_api.settings, "ADMIN_BASE_URL", "https://admin.reputation.motionlabs.kr")
    hospital = _hospital()
    db = FakeDB(hospital)

    with pytest.raises(HTTPException) as exc_info:
        await _connect(db, hospital, raw)

    assert exc_info.value.status_code == 422
    assert "플랫폼 기본 도메인" in exc_info.value.detail
    assert db.committed is False


async def test_connect_domain_taken_by_another_hospital_is_409():
    hospital = _hospital()
    db = FakeDB(hospital, duplicate_owner=SimpleNamespace(name="다른병원의원"))

    with pytest.raises(HTTPException) as exc_info:
        await _connect(db, hospital, "clinic.example.com")

    assert exc_info.value.status_code == 409
    assert "이미 다른 병원" in exc_info.value.detail
    assert hospital.aeo_domain is None
    assert db.committed is False


async def test_connect_domain_same_hospital_resave_is_allowed():
    """같은 병원의 동일 도메인 재저장은 중복이 아니다 (조회는 자기 자신 제외)."""
    hospital = _hospital(aeo_domain="clinic.example.com", site_built=True)
    db = FakeDB(hospital, duplicate_owner=None)

    result = await _connect(db, hospital, "Clinic.Example.com")

    assert hospital.aeo_domain == "clinic.example.com"
    assert "unchanged" in result["detail"]
    # 중복 조회가 정규화된 값으로 실행됐는지 확인
    params = db.statements[0].compile().params
    assert "clinic.example.com" in params.values()
    assert hospital.id in params.values()  # 자기 자신 제외 조건


async def test_connect_domain_persists_management_metadata():
    hospital = _hospital(site_live=True, status=HospitalStatus.ACTIVE)
    db = FakeDB(hospital)

    await _connect(
        db,
        hospital,
        "Clinic.Example.com",
        domain_management_mode="HOSPITAL_MANAGED",
        domain_dns_strategy="APEX_ADDRESS",
        domain_registrar="Gabia",
        domain_dns_provider="Cloudflare",
        domain_purchase_note="Hospital already owns the apex.",
    )

    assert hospital.aeo_domain == "clinic.example.com"
    assert _enum_value(hospital.domain_management_mode) == "HOSPITAL_MANAGED"
    assert _enum_value(hospital.domain_dns_strategy) == "APEX_ADDRESS"
    assert hospital.domain_registrar == "Gabia"
    assert hospital.domain_dns_provider == "Cloudflare"
    assert hospital.domain_purchase_note == "Hospital already owns the apex."
    assert hospital.site_live is False


async def test_connect_domain_strategy_change_resets_live_state_for_same_domain():
    hospital = _hospital(
        aeo_domain="clinic.example.com",
        domain_dns_strategy="CNAME",
        site_live=True,
        site_built=True,
        status=HospitalStatus.ACTIVE,
    )
    db = FakeDB(hospital)

    await _connect(
        db,
        hospital,
        "clinic.example.com",
        domain_dns_strategy="APEX_ADDRESS",
    )

    assert _enum_value(hospital.domain_dns_strategy) == "APEX_ADDRESS"
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert db.committed is True


def test_hospital_detail_serializes_domain_metadata_defaults_for_legacy_rows():
    hospital = _hospital(aeo_domain="clinic.example.com")

    result = hospitals_api._serialize(hospital)

    assert result["domain_management_mode"] == "HOSPITAL_MANAGED"
    assert result["domain_dns_strategy"] == "CNAME"
    assert result["domain_registrar"] is None
    assert result["domain_dns_provider"] is None
    assert result["domain_purchase_note"] is None


async def test_activate_hospital_rejects_apex_when_cname_exists_even_if_address_matches(monkeypatch):
    hospital = _hospital(
        aeo_domain="jangclinic.co.kr",
        domain_dns_strategy=DomainDnsStrategy.APEX_ADDRESS,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        schedule_set=True,
        status=HospitalStatus.PENDING_DOMAIN,
        site_live=False,
    )
    db = FakeDB(hospital)

    async def _fake_check_domain_dns(domain, strategy=DomainDnsStrategy.CNAME):
        assert domain == "jangclinic.co.kr"
        assert strategy == DomainDnsStrategy.APEX_ADDRESS
        return SimpleNamespace(
            verified=False,
            cname_value="target.motionlabs.io",
            address_values=["34.117.10.20"],
            expected_cname="target.motionlabs.io",
            expected_addresses=["34.117.10.20"],
            verification_method=None,
        )

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _fake_check_domain_dns)

    with pytest.raises(HTTPException) as exc_info:
        await hospitals_api.activate_hospital(hospital.id, db=db)

    assert exc_info.value.status_code == 400
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert db.committed is False


async def test_activate_hospital_accepts_apex_address_strategy(monkeypatch):
    hospital = _hospital(
        aeo_domain="jangclinic.co.kr",
        domain_dns_strategy=DomainDnsStrategy.APEX_ADDRESS,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        schedule_set=True,
        status=HospitalStatus.PENDING_DOMAIN,
        site_live=False,
    )
    db = FakeDB(hospital)

    async def _fake_check_domain_dns(domain, strategy=DomainDnsStrategy.CNAME):
        assert domain == "jangclinic.co.kr"
        assert strategy == DomainDnsStrategy.APEX_ADDRESS
        return SimpleNamespace(
            verified=True,
            cname_value=None,
            address_values=["34.117.10.20"],
            expected_cname="target.motionlabs.io",
            expected_addresses=["34.117.10.20"],
            verification_method="address",
        )

    async def _fake_revalidate(*args, **kwargs):
        return None

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _fake_check_domain_dns)
    monkeypatch.setattr(hospitals_api, "ensure_site_revalidate_configured", lambda: None)
    monkeypatch.setattr(hospitals_api, "trigger_hospital_site_revalidate_safe", _fake_revalidate)

    result = await hospitals_api.activate_hospital(hospital.id, db=db)

    assert result == {"detail": "장편한외과의원 activated"}
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert db.committed is True
    detail = db.added[0].detail
    assert detail["aeo_domain"] == "jangclinic.co.kr"
    assert detail["verification_method"] == "address"


async def test_activate_hospital_subdomain_default_without_custom_domain(monkeypatch):
    """자기 도메인 미연결 시 DNS 검증 없이 기본 서브도메인으로 라이브한다 (하이브리드)."""
    hospital = _hospital(
        aeo_domain=None,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        schedule_set=True,
        status=HospitalStatus.PENDING_DOMAIN,
        site_live=False,
    )
    db = FakeDB(hospital)

    async def _must_not_call(*args, **kwargs):
        raise AssertionError("자기 도메인이 없으면 check_domain_dns를 호출하면 안 된다")

    async def _fake_revalidate(*args, **kwargs):
        return None

    monkeypatch.setattr(hospitals_api, "check_domain_dns", _must_not_call)
    monkeypatch.setattr(hospitals_api, "ensure_site_revalidate_configured", lambda: None)
    monkeypatch.setattr(hospitals_api, "trigger_hospital_site_revalidate_safe", _fake_revalidate)

    result = await hospitals_api.activate_hospital(hospital.id, db=db)

    assert result == {"detail": "장편한외과의원 activated"}
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert db.committed is True
    detail = db.added[0].detail
    assert detail["aeo_domain"] is None
    assert detail["verification_method"] == "platform_subdomain"
