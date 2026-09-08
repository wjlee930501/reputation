"""공식 채널 URL을 저장하면 그 자리에서 근거 자료 행이 생기고 본문은 워커가 받아 온다(설계 §4.3).

'자료로 추가' 버튼은 없다. 저장 요청은 fetch를 기다리지 않고, 응답의 QUEUED는 커밋된 행이
실제로 있다는 뜻이다. 지난번 등록이 실패해 행이 없다면 같은 값을 다시 저장하는 것만으로
회복된다.
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


class FakeDB:
    """자료 중복 조회와 병원 advisory lock만 구분하면 되는 최소 세션."""

    def __init__(self, hospital, existing_sources=()):
        self.hospital = hospital
        #: (source_id, url) 튜플 — 제외되지 않은 기존 자료.
        self.existing_sources = list(existing_sources)
        self.handoff = SimpleNamespace(state=HandoffState.HANDOFF_ACCEPTED)
        self.added = []
        self.commits = 0
        self.rolled_back = False

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, stmt):
        text = str(stmt)
        if "hospital_source_assets" in text:
            return SimpleNamespace(all=lambda: list(self.existing_sources))
        return SimpleNamespace(
            scalar_one=lambda: None,
            scalar=lambda: None,
            scalar_one_or_none=lambda: self.handoff,
            all=list,
        )

    async def scalar(self, stmt):
        return None

    async def get(self, _model, object_id):
        return self.hospital if self.hospital.id == object_id else None

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        # 실제 flush처럼 기본값(uuid)을 채워 준다 — 응답이 source_id를 실어야 한다.
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1
        await self.flush()

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
def worker(monkeypatch):
    """워커 발행과 V0 큐잉을 대신한다 — 네트워크·큐를 쓰지 않는다."""
    calls = {"dispatch": [], "fetch": []}

    def fake_send_task(name, args=None, **_kwargs):
        calls["dispatch"].append((name, list(args or [])))

    async def fail_if_fetched(url):
        calls["fetch"].append(url)
        raise AssertionError("저장 요청은 URL을 fetch하지 않는다")

    async def fake_operation_dispatch(_db, _command, _task):
        return None

    monkeypatch.setattr(hospitals_api.celery_app, "send_task", fake_send_task)
    monkeypatch.setattr(essence_sources_service, "fetch_url_text", fail_if_fetched)
    monkeypatch.setattr(hospitals_api, "dispatch_operation", fake_operation_dispatch)
    return calls


async def _patch(db, hospital, **fields):
    return await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(**fields),
        BackgroundTasks(),
        db=db,
    )


async def test_new_website_url_creates_a_pending_row_and_hands_the_fetch_to_a_worker(worker):
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    sources = db.sources()
    assert len(sources) == 1
    source = sources[0]
    assert source.source_type == SourceType.HOMEPAGE
    assert source.url == "https://clinic.example.com"
    assert source.status == SourceStatus.PENDING
    assert source.raw_text is None
    assert source.source_metadata["channel_field"] == "website_url"
    assert source.source_metadata["fetch_state"] == "QUEUED"
    assert source.source_metadata["registered_from"] == "profile"
    assert source.source_metadata["normalized_url"] == "https://clinic.example.com"
    # 요청 안에서 fetch하지 않는다 — 채널 한 곳에 12초까지 걸리기 때문이다.
    assert worker["fetch"] == []
    assert worker["dispatch"] == [
        ("app.workers.tasks.fetch_channel_source", [str(source.id)]),
    ]
    assert result["source_registration"] == [
        {
            "field": "website_url",
            "status": "QUEUED",
            "source_id": source.id,
            "message": None,
        }
    ]
    audits = db.audits("profile_channel_source_registered")
    assert len(audits) == 1
    assert audits[0].detail["field"] == "website_url"
    assert audits[0].detail["status"] == "QUEUED"


async def test_blog_url_registers_as_naver_blog(worker):
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, blog_url="https://blog.naver.com/clinic")

    assert [source.source_type for source in db.sources()] == [SourceType.NAVER_BLOG]
    assert [entry["status"] for entry in result["source_registration"]] == ["QUEUED"]


async def test_resending_the_same_url_registers_nothing(worker):
    """값도 그대로고 자료도 이미 있으면 이번 저장이 한 일이 없다."""
    hospital = _hospital(website_url="https://clinic.example.com")
    db = FakeDB(
        hospital, existing_sources=[(uuid.uuid4(), "https://clinic.example.com")]
    )

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert db.sources() == []
    assert worker["dispatch"] == []
    assert result["source_registration"] == []


@pytest.mark.parametrize(
    "stored_url",
    [
        "https://clinic.example.com/",
        "https://WWW.Clinic.example.com",
        "https://www.clinic.example.com/",
    ],
)
async def test_equivalent_urls_are_not_registered_twice(worker, stored_url):
    """끝 슬래시·대소문자·www만 다른 주소는 같은 페이지다 — 행을 두 번 만들지 않는다."""
    existing_id = uuid.uuid4()
    hospital = _hospital()
    db = FakeDB(hospital, existing_sources=[(existing_id, stored_url)])

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert db.sources() == []
    entry = result["source_registration"][0]
    assert entry["status"] == "SKIPPED"
    assert entry["source_id"] == existing_id
    assert entry["message"]


async def test_resaving_after_a_failed_registration_dispatches_again(worker):
    """지난번 등록이 실패해 자료 행이 없으면, 같은 값을 다시 저장하는 것만으로 회복된다."""
    hospital = _hospital(website_url="https://clinic.example.com")
    db = FakeDB(hospital, existing_sources=[])

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert len(db.sources()) == 1
    assert [entry["status"] for entry in result["source_registration"]] == ["QUEUED"]
    assert len(worker["dispatch"]) == 1


async def test_excluded_rows_do_not_block_a_new_registration(worker):
    """제외한 자료는 근거가 아니다 — 같은 주소를 다시 저장하면 새로 등록한다."""
    hospital = _hospital()
    # 제외 행은 조회에서 이미 빠진다(status != EXCLUDED).
    db = FakeDB(hospital, existing_sources=[])

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert len(db.sources()) == 1
    assert result["source_registration"][0]["status"] == "QUEUED"


async def test_a_row_that_could_not_be_created_reports_failed(monkeypatch, worker):
    """응답의 FAILED는 '행조차 만들지 못했다'는 뜻이다 — 커밋된 행을 실패라 말하지 않는다."""

    async def failing_create(*_args, **_kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(hospitals_api, "create_pending_channel_source", failing_create)
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, website_url="https://clinic.example.com")

    assert hospital.website_url == "https://clinic.example.com"
    assert db.rolled_back is True
    assert worker["dispatch"] == []
    entry = result["source_registration"][0]
    assert entry == {
        "field": "website_url",
        "status": "FAILED",
        "source_id": None,
        "message": "자료 등록에 실패했습니다. 자료 화면에서 직접 등록해 주세요.",
    }
    audits = db.audits("profile_channel_source_registered")
    assert len(audits) == 1
    assert audits[0].detail["status"] == "FAILED"


@pytest.mark.parametrize(
    "field,url",
    [
        ("naver_place_url", "https://naver.me/changed"),
        ("google_business_profile_url", "https://g.page/clinic"),
        ("google_maps_url", "https://maps.google.com/changed"),
        ("kakao_channel_url", "https://pf.kakao.com/clinic"),
    ],
)
async def test_profile_only_channels_never_register(worker, field, url):
    assert field in hospitals_api.PROFILE_ONLY_CHANNEL_FIELDS
    hospital = _hospital()
    db = FakeDB(hospital)

    result = await _patch(db, hospital, **{field: url})

    assert db.sources() == []
    assert worker["dispatch"] == []
    assert result["source_registration"] == []


def test_registration_error_message_is_safe_and_typed():
    error = essence_sources_service.SourceRegistrationError("제목을 찾지 못했습니다.", status_code=422)
    assert error.status_code == 422
    assert error.message == "제목을 찾지 못했습니다."


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://clinic.example.com/", "https://clinic.example.com"),
        ("HTTPS://WWW.Clinic.Example.com/path/", "https://clinic.example.com/path"),
        ("https://clinic.example.com/a?b=1#frag", "https://clinic.example.com/a?b=1"),
        ("  https://clinic.example.com  ", "https://clinic.example.com"),
        ("", ""),
    ],
)
def test_url_normalization_only_folds_what_points_at_the_same_page(url, expected):
    assert essence_sources_service.normalize_source_url(url) == expected
