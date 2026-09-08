"""공식 채널 URL을 저장하면 그 자리에서 근거 자료로 등록·처리된다(설계 §4.3).

'자료로 추가' 버튼은 없다. 저장이 곧 등록이므로, 저장 성공은 등록 실패로 되돌아가지
않고 결과만 응답에 실린다.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app.api.admin import hospitals as hospitals_api
from app.models.audit import AdminAuditLog
from app.models.essence import HospitalSourceAsset, SourceStatus, SourceType
from app.models.handoff import HandoffState
from app.services import essence_sources as essence_sources_service
from app.services.asset_extractor import FetchQuality


class FakeDB:
    """자료 중복 조회와 병원 advisory lock만 구분하면 되는 최소 세션."""

    def __init__(self, hospital, existing_source_id=None):
        self.hospital = hospital
        self.existing_source_id = existing_source_id
        self.handoff = SimpleNamespace(state=HandoffState.HANDOFF_ACCEPTED)
        self.added = []
        self.commits = 0
        self.rolled_back = False

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, stmt):
        return SimpleNamespace(
            scalar_one=lambda: None,
            scalar=lambda: None,
            scalar_one_or_none=lambda: self.handoff,
        )

    async def scalar(self, stmt):
        # 자료 중복 조회만 이 경로로 온다.
        assert "hospital_source_assets" in str(stmt)
        return self.existing_source_id

    async def get(self, _model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1
        # 실제 flush처럼 서버 기본값(uuid)을 채워 준다 — 응답이 source_id를 실어야 한다.
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _item):
        pass

    def sources(self):
        return [item for item in self.added if isinstance(item, HospitalSourceAsset)]

    def audits(self, action):
        return [
            item
            for item in self.added
            if isinstance(item, AdminAuditLog) and item.action == action
        ]


def _hospital(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="테스트의원",
        slug="test-clinic",
        status="ONBOARDING",
        plan=None,
        source_lead_id=None,
        onboarding_note=None,
        site_live=False,
        site_built=False,
        profile_complete=False,
        v0_report_done=False,
        schedule_set=False,
        created_at=None,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        competitors=[],
        director_name="김원장",
        director_career="외과 전문의",
        director_philosophy="충분히 설명합니다.",
        director_credentials=None,
        address="서울 성동구",
        phone="02-000-0000",
        business_hours={"mon": "09:00-18:00"},
        website_url=None,
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url="https://maps.google.com/example",
        naver_place_url="https://naver.me/example",
        aeo_domain=None,
        latitude=37.5,
        longitude=127.0,
        wikidata_qid=None,
        gbp_place_id=None,
        naver_place_id=None,
        kakao_place_id=None,
        hira_org_id=None,
        treatments=[{"name": "치질 수술", "description": None}],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def crawl(monkeypatch):
    """fetch와 처리 디스패치를 대신한다 — 네트워크·큐를 쓰지 않는다."""
    calls = {"fetch": [], "dispatch": []}

    async def fake_fetch(url):
        calls["fetch"].append(url)
        return "병원 소개 본문", None, FetchQuality(200, False, 0.0, "병원 홈페이지")

    async def fake_dispatch(_db, *, hospital_id, source_ids, source_identities=None):
        calls["dispatch"].append((hospital_id, list(source_ids)))
        return None

    async def fake_operation_dispatch(_db, _command, _task):
        return None

    monkeypatch.setattr(essence_sources_service, "fetch_url_text", fake_fetch)
    monkeypatch.setattr(
        essence_sources_service, "start_source_processing_best_effort", fake_dispatch
    )
    monkeypatch.setattr(hospitals_api, "dispatch_operation", fake_operation_dispatch)
    return calls


async def _patch(db, hospital, **fields):
    return await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(**fields),
        BackgroundTasks(),
        db=db,
    )


async def test_new_website_url_becomes_a_processed_source(crawl):
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    sources = db.sources()
    assert len(sources) == 1
    source = sources[0]
    assert source.source_type == SourceType.HOMEPAGE
    assert source.url == "https://clinic.example.com"
    assert source.status == SourceStatus.PENDING
    assert crawl["dispatch"] == [(hospital.id, [source.id])]
    assert result["source_registration"] == [
        {
            "field": "website_url",
            "status": "REGISTERED",
            "source_id": source.id,
            "message": None,
        }
    ]
    audits = db.audits("profile_channel_source_registered")
    assert len(audits) == 1
    assert audits[0].detail["field"] == "website_url"
    assert audits[0].detail["status"] == "REGISTERED"


async def test_blog_url_registers_as_naver_blog(crawl):
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, blog_url="https://blog.naver.com/clinic")

    assert [source.source_type for source in db.sources()] == [SourceType.NAVER_BLOG]
    assert [entry["status"] for entry in result["source_registration"]] == ["REGISTERED"]


async def test_resending_the_same_url_registers_nothing(crawl):
    """바뀐 필드만 등록 대상이다 — 프로파일 전체 PATCH가 매번 재크롤을 부르면 안 된다."""
    hospital = _hospital(website_url="https://clinic.example.com")
    db = FakeDB(hospital)

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert db.sources() == []
    assert crawl["fetch"] == []
    assert crawl["dispatch"] == []
    assert result["source_registration"] == []


async def test_url_already_registered_as_a_source_is_skipped(crawl):
    existing_id = uuid.uuid4()
    hospital = _hospital()
    db = FakeDB(hospital, existing_source_id=existing_id)

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert db.sources() == []
    assert crawl["fetch"] == []
    entry = result["source_registration"][0]
    assert entry["status"] == "SKIPPED"
    assert entry["source_id"] == existing_id
    assert entry["message"]


async def test_fetch_failure_keeps_the_profile_saved(monkeypatch, crawl):
    async def failing_fetch(_url):
        return "", "연결할 수 없습니다", None

    monkeypatch.setattr(essence_sources_service, "fetch_url_text", failing_fetch)
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert hospital.website_url == "https://clinic.example.com"
    assert db.sources() == []
    entry = result["source_registration"][0]
    assert entry == {
        "field": "website_url",
        "status": "FAILED",
        "source_id": None,
        "message": "URL 크롤링 실패: 연결할 수 없습니다",
    }
    audits = db.audits("profile_channel_source_registered")
    assert len(audits) == 1
    assert audits[0].detail["status"] == "FAILED"
    assert audits[0].detail["message"] == entry["message"]


@pytest.mark.parametrize(
    "field,url",
    [
        ("naver_place_url", "https://naver.me/changed"),
        ("google_business_profile_url", "https://g.page/clinic"),
        ("google_maps_url", "https://maps.google.com/changed"),
        ("kakao_channel_url", "https://pf.kakao.com/clinic"),
    ],
)
async def test_profile_only_channels_never_register(crawl, field, url):
    assert field in hospitals_api.PROFILE_ONLY_CHANNEL_FIELDS
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, **{field: url})

    assert db.sources() == []
    assert crawl["fetch"] == []
    assert result["source_registration"] == []


async def test_registration_error_message_is_safe_and_typed():
    error = essence_sources_service.SourceRegistrationError("제목을 찾지 못했습니다.", status_code=422)
    assert error.status_code == 422
    assert error.message == "제목을 찾지 못했습니다."
