"""P1-6 — POST /domain/verify가 operations verify-domain과 동일한 LIVE 게이트를 갖는지."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import domain as domain_api
from app.models.hospital import HospitalStatus


class FakeDB:
    def __init__(self, hospital):
        self.hospital = hospital
        self.committed = False

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    async def commit(self):
        self.committed = True


def _hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="테스트의원",
        status=HospitalStatus.PENDING_DOMAIN,
        aeo_domain="clinic.example.com",
        v0_report_done=True,
        site_built=True,
        schedule_set=True,
        site_live=False,
        profile_complete=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_dns(monkeypatch, cname="target.motionlabs.io"):
    monkeypatch.setattr(domain_api.settings, "CNAME_TARGET", "target.motionlabs.io")

    async def _fake_resolve(domain):
        return cname

    monkeypatch.setattr(domain_api, "_resolve_cname", _fake_resolve)


async def test_verify_domain_activates_when_all_prerequisites_met(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    response = await domain_api.verify_domain(hospital.id, db=db)

    assert response.verified is True
    assert response.domain == "clinic.example.com"
    assert response.expected_cname == "target.motionlabs.io"
    assert hospital.site_live is True
    assert hospital.status == HospitalStatus.ACTIVE
    assert db.committed is True


@pytest.mark.parametrize(
    "overrides,expected_label",
    [
        ({"v0_report_done": False}, "V0 리포트"),
        ({"site_built": False}, "병원 정보 허브 빌드"),
        ({"schedule_set": False}, "콘텐츠 스케줄"),
    ],
)
async def test_verify_domain_blocks_live_without_prerequisites(monkeypatch, overrides, expected_label):
    """DNS가 맞아도 사전 단계 미충족이면 409 — 게이트 우회 경로 차단 (P1-6)."""
    hospital = _hospital(**overrides)
    db = FakeDB(hospital)
    _patch_dns(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await domain_api.verify_domain(hospital.id, db=db)

    assert exc_info.value.status_code == 409
    assert expected_label in exc_info.value.detail
    assert hospital.site_live is False
    assert hospital.status == HospitalStatus.PENDING_DOMAIN
    assert db.committed is False


async def test_verify_domain_keeps_response_shape_on_cname_mismatch(monkeypatch):
    """CNAME 불일치는 기존 응답 형태(verified=False + 안내 메시지)를 유지한다."""
    hospital = _hospital(v0_report_done=False)  # 게이트보다 DNS 실패가 먼저
    db = FakeDB(hospital)
    _patch_dns(monkeypatch, cname="wrong.example.net")

    response = await domain_api.verify_domain(hospital.id, db=db)

    assert response.verified is False
    assert "CNAME 검증 실패" in response.message
    assert hospital.site_live is False
    assert db.committed is False


async def test_verify_domain_requires_domain_set():
    hospital = _hospital(aeo_domain=None)
    db = FakeDB(hospital)

    with pytest.raises(HTTPException) as exc_info:
        await domain_api.verify_domain(hospital.id, db=db)

    assert exc_info.value.status_code == 400
