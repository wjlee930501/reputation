"""Test default-address activation independence from custom domain.

DM-F3: Default-address go-live and custom-domain connect are independent.
기본 주소 활성화는 커스텀 도메인 설정 여부와 무관하게 가능해야 한다.
"""

import uuid
from unittest.mock import patch

import pytest

from app.api.admin.hospitals import activate_hospital
from app.models.hospital import Hospital, HospitalStatus


class FakeDB:
    """Minimal async DB mock for activation tests."""
    
    def __init__(self, hospital):
        self.hospital = hospital
        self.committed = False
        self.added = []

    async def get(self, model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    async def scalar(self, stmt):
        # Mock service interval check - return None for new activation
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True


def make_hospital(
    slug="test-clinic",
    status=HospitalStatus.PENDING_DOMAIN,
    profile_complete=True,
    v0_report_done=True,
    site_built=True,
    site_live=False,
    aeo_domain=None,
):
    """Create test hospital."""
    return Hospital(
        id=uuid.uuid4(),
        name="테스트의원",
        slug=slug,
        aeo_domain=aeo_domain,
        status=status,
        profile_complete=profile_complete,
        v0_report_done=v0_report_done,
        site_built=site_built,
        site_live=site_live,
    )


@pytest.mark.asyncio
async def test_activate_without_custom_domain():
    """기본 플랫폼 주소만으로 활성화 가능 (커스텀 도메인 없음)."""
    hospital = make_hospital(aeo_domain=None)
    db = FakeDB(hospital)
    
    # Mock activation gate to pass
    with patch("app.api.admin.hospitals.evaluate_activation_gate") as mock_gate:
        mock_gate.return_value = {"ready": True}
        
        # Mock site revalidate
        with patch("app.api.admin.hospitals.ensure_site_revalidate_configured"):
            with patch("app.api.admin.hospitals.trigger_hospital_site_revalidate_safe"):
                result = await activate_hospital(hospital.id, db)
    
    # 활성화 성공
    assert hospital.status == HospitalStatus.ACTIVE
    assert hospital.site_live is True
    assert result["site_live"] is True
    assert db.committed is True


@pytest.mark.asyncio
async def test_activate_with_pending_custom_domain():
    """DM-F3: 커스텀 도메인이 설정되어 있어도 기본 주소로 활성화 가능."""
    hospital = make_hospital(aeo_domain="ai.testclinic.co.kr")
    db = FakeDB(hospital)
    
    # Mock activation gate to pass
    with patch("app.api.admin.hospitals.evaluate_activation_gate") as mock_gate:
        mock_gate.return_value = {"ready": True}
        
        # Mock site revalidate
        with patch("app.api.admin.hospitals.ensure_site_revalidate_configured"):
            with patch("app.api.admin.hospitals.trigger_hospital_site_revalidate_safe"):
                result = await activate_hospital(hospital.id, db)
    
    # 커스텀 도메인이 있지만 기본 주소로 활성화 성공
    assert hospital.status == HospitalStatus.ACTIVE
    assert hospital.site_live is True
    assert hospital.aeo_domain == "ai.testclinic.co.kr"  # 커스텀 도메인은 그대로 유지
    assert result["site_live"] is True
    assert db.committed is True


@pytest.mark.asyncio
async def test_activate_respects_activation_gate():
    """활성화 게이트는 여전히 검증됨 (profile_complete, v0_report_done, site_built)."""
    hospital = make_hospital(
        profile_complete=False,  # 게이트 조건 미달
        aeo_domain=None,
    )
    db = FakeDB(hospital)
    
    # Mock activation gate to fail
    with patch("app.api.admin.hospitals.evaluate_activation_gate") as mock_gate:
        mock_gate.return_value = {
            "ready": False,
            "missing": ["프로파일 입력 완료"],
        }
        
        # HTTPException 발생 예상
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await activate_hospital(hospital.id, db)
    
    # 409 오류 반환
    assert exc_info.value.status_code == 409
    assert hospital.site_live is False  # 활성화 안 됨
