"""PATCH /admin/hospitals/{id}/domain 입력 강화 —
정규화 저장, 형식/플랫폼 도메인 422(한국어), 타 병원 중복 409(한국어).
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import hospitals as hospitals_api
from app.models.hospital import HospitalStatus


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
        site_live=False,
        site_built=False,
        aeo_domain=None,
        treatments=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _connect(db, hospital, raw_domain):
    return await hospitals_api.connect_domain(
        hospital.id,
        hospitals_api.DomainConnect(domain=raw_domain),
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


@pytest.mark.parametrize("raw", ["aeo.motionlabs.io", "AEO.Motionlabs.IO", "clinic.aeo.motionlabs.io"])
async def test_connect_domain_rejects_platform_domain(monkeypatch, raw):
    """CNAME_TARGET(및 그 하위 호스트)은 병원 도메인으로 저장할 수 없다."""
    monkeypatch.setattr(hospitals_api.settings, "CNAME_TARGET", "aeo.motionlabs.io")
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
