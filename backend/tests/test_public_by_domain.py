"""커스텀 도메인 → 병원 역조회 (GET /api/v1/public/site/hospitals/by-domain/{domain})
+ 도메인 정규화 유틸 (조회/저장 공용 규칙).
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.public import site as site_api
from app.models.hospital import HospitalStatus
from app.utils.domain import is_valid_hostname, normalize_domain

# slowapi @limiter.limit 우회 — 단위 테스트는 FastAPI 요청 라이프사이클 밖에서 실행된다.
_get_by_domain = site_api.get_hospital_by_domain.__wrapped__


# ── 정규화 유틸 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("clinic.example.com", "clinic.example.com"),
        ("Clinic.Example.COM", "clinic.example.com"),          # 대소문자
        ("clinic.example.com.", "clinic.example.com"),          # 끝 점
        ("clinic.example.com:443", "clinic.example.com"),       # 포트
        ("https://clinic.example.com/path?q=1#f", "clinic.example.com"),  # 스킴/경로
        ("  Clinic.Example.COM:8080/  ", "clinic.example.com"),
        ("user@clinic.example.com", "clinic.example.com"),      # userinfo
        ("", None),
        (None, None),
        ("...", None),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


def test_is_valid_hostname():
    assert is_valid_hostname("info.jangpyeon.com") is True
    assert is_valid_hostname("clinic.example.co.kr") is True
    assert is_valid_hostname("localhost") is False          # 단일 레이블 거부
    assert is_valid_hostname("clinic_example.com") is False  # 밑줄 불가
    assert is_valid_hostname("a" * 300 + ".com") is False    # 길이 초과
    assert is_valid_hostname(None) is False
    assert is_valid_hostname("") is False


# ── 엔드포인트 ────────────────────────────────────────────────────


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
    def __init__(self, hospital=None):
        self._hospital = hospital
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._hospital)


def _request():
    return SimpleNamespace()


def _hospital():
    return SimpleNamespace(
        slug="jang-clinic",
        name="장편한외과의원",
        aeo_domain="clinic.example.com",
        status=HospitalStatus.ACTIVE,
        site_live=True,
    )


async def test_by_domain_returns_exact_contract_shape():
    db = FakeDB(_hospital())

    response = await _get_by_domain(_request(), "clinic.example.com", db=db)

    # /site 미들웨어가 의존하는 정확한 응답 계약 — 필드 추가/누락 금지.
    assert response == {
        "slug": "jang-clinic",
        "name": "장편한외과의원",
        "aeo_domain": "clinic.example.com",
    }


async def test_by_domain_normalizes_path_param_before_lookup():
    """대소문자/포트/끝 점이 섞인 호스트도 정규화 후 소문자 동등 비교로 조회한다."""
    db = FakeDB(_hospital())

    await _get_by_domain(_request(), "Clinic.Example.COM:443.", db=db)

    params = db.statements[0].compile().params
    assert "clinic.example.com" in params.values()
    assert HospitalStatus.ACTIVE in params.values()  # ACTIVE 필터가 SQL에 포함


async def test_by_domain_unknown_domain_is_404():
    db = FakeDB(None)  # ACTIVE + site_live 매칭 행 없음 (비활성/미라이브 포함)

    with pytest.raises(HTTPException) as exc_info:
        await _get_by_domain(_request(), "nobody.example.com", db=db)

    assert exc_info.value.status_code == 404


async def test_by_domain_unnormalizable_input_is_404_without_db_hit():
    db = FakeDB(_hospital())

    with pytest.raises(HTTPException) as exc_info:
        await _get_by_domain(_request(), "...", db=db)

    assert exc_info.value.status_code == 404
    assert db.statements == []  # 빈 호스트로 DB 조회하지 않는다


# ── 하이브리드 기본 서브도메인({slug}.{platform host}) ────────────────


async def test_by_domain_resolves_platform_subdomain_by_slug():
    """{slug}.{platform host} 는 자기 도메인 없이 slug로 직접 해석된다 (하이브리드 기본)."""
    db = FakeDB(_hospital())

    response = await _get_by_domain(_request(), "jang-clinic.reputation.motionlabs.kr", db=db)

    assert response["slug"] == "jang-clinic"
    params = db.statements[0].compile().params
    assert "jang-clinic" in params.values()       # aeo_domain이 아니라 slug로 조회
    assert HospitalStatus.ACTIVE in params.values()  # ACTIVE + site_live 게이트 유지


async def test_by_domain_reserved_platform_label_uses_aeo_domain_path():
    """admin/www 등 예약 라벨은 slug로 오인하지 않고 aeo_domain 경로로 빠진다."""
    db = FakeDB(None)

    with pytest.raises(HTTPException) as exc_info:
        await _get_by_domain(_request(), "admin.reputation.motionlabs.kr", db=db)

    assert exc_info.value.status_code == 404
    params = db.statements[0].compile().params
    # slug "admin"이 아니라 정규화된 전체 호스트로 aeo_domain 비교.
    assert "admin.reputation.motionlabs.kr" in params.values()


async def test_by_domain_multi_label_subdomain_uses_aeo_domain_path():
    """{a}.{b}.{platform host} 같은 다중 라벨은 slug 경로가 아니다 (단일 라벨만 기본)."""
    db = FakeDB(None)

    with pytest.raises(HTTPException) as exc_info:
        await _get_by_domain(_request(), "a.b.reputation.motionlabs.kr", db=db)

    assert exc_info.value.status_code == 404
    params = db.statements[0].compile().params
    assert "a.b.reputation.motionlabs.kr" in params.values()
