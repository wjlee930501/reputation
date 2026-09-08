"""프로파일 저장 — 완료 파생, 남은 필수 항목, 공개 운영 중 비우기 차단(P2-10)."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import hospitals as hospitals_api
from app.models.handoff import HandoffState
from app.models.hospital import HospitalStatus
from app.services.essence_readiness import EssenceReadinessState
from app.services.hospital_geocoding import GeocodeResult, GeocodingError


class FakeDB:
    def __init__(self, hospital, handoff=None):
        self.hospital = hospital
        self.handoff = handoff
        self.added = []
        self.committed = False
        #: 잡힌 병원 advisory lock. 첫 읽기보다 먼저 잡혔는지까지 확인한다.
        self.locks: list[uuid.UUID] = []
        self.locked_before_first_read = None

    def get_bind(self):
        # advisory lock 헬퍼는 Postgres 바인딩에서만 동작한다 — 잠금 호출을 관찰하려면 필요하다.
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, stmt):
        # 병원 advisory lock과, 완료 전환 시의 인수 조회만 이 fake에 온다.
        if "pg_advisory_xact_lock" in str(stmt):
            self.locks.append(self.hospital.id)
        return SimpleNamespace(
            scalar_one=lambda: None,
            scalar=lambda: None,
            scalar_one_or_none=lambda: self.handoff,
        )

    async def get(self, model, object_id):
        if self.locked_before_first_read is None:
            self.locked_before_first_read = bool(self.locks)
        return self.hospital if self.hospital.id == object_id else None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        pass


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
        profile_complete=True,
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
        website_url="https://clinic.example.com",
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


async def test_profile_complete_is_derived_from_requirements(monkeypatch):
    """완료는 body가 아니라 필수 8항목에서 파생되고, 그때만 파이프라인이 열린다."""
    hospital = _hospital(
        profile_complete=False,
        director_philosophy=None,
        treatments=[],
    )
    db = FakeDB(hospital, handoff=SimpleNamespace(state=HandoffState.HANDOFF_ACCEPTED))
    background = BackgroundTasks()
    dispatched = []

    async def _dispatch(_db, command, _task):
        dispatched.append(command.operation_type)
        return None

    monkeypatch.setattr(hospitals_api, "dispatch_operation", _dispatch)

    result = await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(
            director_philosophy="충분히 설명합니다.",
            treatments=[{"name": "치질 수술"}],
        ),
        background,
        db=db,
    )

    assert result["profile_complete"] is True
    assert result["missing_profile_requirements"] == []
    assert hospital.profile_complete is True
    assert dispatched == ["TRIGGER_V0_REPORT"]
    assert len(background.tasks) == 1
    site_task = background.tasks[0]
    assert getattr(getattr(site_task.func, "__self__", None), "name", None) == (
        hospitals_api.build_aeo_site.name
    )
    assert site_task.kwargs["args"] == [str(hospital.id)]


async def test_missing_requirements_are_labelled():
    """남은 항목은 화면이 그대로 쓸 수 있게 키와 라벨로 내려간다."""
    hospital = _hospital(profile_complete=False, treatments=[])
    db = FakeDB(hospital)

    result = await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(phone="02-111-2222"),
        BackgroundTasks(),
        db=db,
    )

    assert result["profile_complete"] is False
    assert result["missing_profile_requirements"] == [{"key": "treatments", "label": "진료 항목"}]
    assert db.committed is True


async def test_body_profile_complete_is_ignored_and_derivation_wins(caplog):
    """body의 완료 플래그는 버리고 경고만 남긴다 — 저장 결과에서 파생한 값이 이긴다.

    배포 순서가 api → admin이라, 이미 열려 있던 옛 탭은 병원 스냅샷째로 보낸다. 그 요청을
    422로 되돌리면 운영자만 저장에 실패한다. 값 자체는 절대 반영하지 않는다.
    """
    assert "profile_complete" not in hospitals_api.HospitalProfileUpdate.model_fields

    with caplog.at_level("WARNING"):
        body = hospitals_api.HospitalProfileUpdate(profile_complete=True, treatments=[])

    assert "profile_complete" not in body.model_fields_set
    assert "profile_complete in request body is ignored" in caplog.text

    hospital = _hospital(profile_complete=False, treatments=[])
    result = await hospitals_api.update_profile(
        hospital.id, body, BackgroundTasks(), db=FakeDB(hospital)
    )

    assert result["profile_complete"] is False
    assert hospital.profile_complete is False


def test_unknown_fields_are_dropped_during_the_admin_transition():
    """옛 화면이 보내는 응답 전용 필드는 저장 대상이 아니지만 요청을 깨지도 않는다."""
    body = hospitals_api.HospitalProfileUpdate(name="테스트의원", phone="02-111-2222")

    assert body.model_fields_set == {"phone"}


async def test_address_change_geocodes_once_and_persists_coordinates(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)
    calls = []

    async def fake_geocode(address):
        calls.append(address)
        return GeocodeResult(37.566535, 126.977969)

    monkeypatch.setattr(hospitals_api, "geocode_address", fake_geocode)

    result = await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(address="서울 중구 세종대로 110"),
        BackgroundTasks(),
        db=db,
    )

    assert calls == ["서울 중구 세종대로 110"]
    assert hospital.latitude == 37.566535
    assert hospital.longitude == 126.977969
    assert result["latitude"] == 37.566535


async def test_address_geocode_failure_is_concrete_and_does_not_save(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)

    async def fail_geocode(_address):
        raise GeocodingError("입력한 주소에서 좌표를 찾지 못했습니다.")

    monkeypatch.setattr(hospitals_api, "geocode_address", fail_geocode)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(
            hospital.id,
            hospitals_api.HospitalProfileUpdate(address="잘못된 주소"),
            BackgroundTasks(),
            db=db,
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "ADDRESS_GEOCODE_FAILED"
    assert "좌표를 찾지 못했습니다" in exc.value.detail["message"]
    assert db.committed is False


async def test_advanced_manual_coordinates_skip_address_geocode(monkeypatch):
    hospital = _hospital()
    db = FakeDB(hospital)

    async def unexpected_geocode(_address):
        raise AssertionError("manual coordinates must not call the provider")

    monkeypatch.setattr(hospitals_api, "geocode_address", unexpected_geocode)

    await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(
            address="서울 중구 직접 확인 주소",
            latitude=37.1,
            longitude=127.1,
            geocode_address=False,
        ),
        BackgroundTasks(),
        db=db,
    )

    assert hospital.latitude == 37.1
    assert hospital.longitude == 127.1


def test_list_serializer_includes_custom_domain_for_admin_search():
    hospital = _hospital(aeo_domain="jangclinic.kr", site_built=True, site_live=True)

    payload = hospitals_api._serialize_list(
        hospital,
        readiness_state=EssenceReadinessState(
            current=True, unprocessed_sources=0, required_sources=1, escalated_draft=False
        ),
        open_exception_count=0,
        ae_owner=None,
    )

    assert payload["aeo_domain"] == "jangclinic.kr"


def test_detail_serializes_visual_approval_missing():
    """상세와 목록이 같은 시각 승인 판정을 내려야 화면이 갈리지 않는다(O-2)."""
    hospital = _hospital()

    detail = hospitals_api._serialize(hospital)
    listed = hospitals_api._serialize_list(
        hospital,
        readiness_state=EssenceReadinessState(
            current=True, unprocessed_sources=0, required_sources=1, escalated_draft=False
        ),
        open_exception_count=0,
        ae_owner=None,
    )

    assert detail["visual_approval_missing"] == listed["visual_approval_missing"]
    assert "공식 로고" in detail["visual_approval_missing"]


@pytest.mark.parametrize(
    "patch_body",
    [
        {"keywords": []},
        {"region": []},
        {"specialties": []},
        {"address": ""},
        {"director_name": ""},
    ],
)
async def test_patch_cannot_unset_profile_complete_while_publicly_serving(patch_body):
    """M-13: 공개 게이트가 profile_complete를 요구하므로 필수 항목을 비우면 공개 페이지가 조용히 404가 된다."""
    hospital = _hospital(status=HospitalStatus.ACTIVE, site_live=True, profile_complete=True)
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(**patch_body)

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "PROFILE_COMPLETE_REQUIRED_WHILE_LIVE"
    # 어떤 칸이 문제인지 말하고, 일시정지하라는 우회 지시는 하지 않는다.
    assert exc.value.detail["missing"]
    for label in exc.value.detail["missing"]:
        assert label in exc.value.detail["message"]
    assert "일시정지" not in exc.value.detail["message"]
    assert db.committed is False
    # 판정 자체가 잠금 아래서 일어나야 재개(`/resume`)와 교차하지 않는다.
    assert db.locks == [hospital.id]
    assert db.locked_before_first_read is True


async def test_patch_can_unset_profile_complete_when_paused():
    hospital = _hospital(status=HospitalStatus.PAUSED, site_live=True, profile_complete=True)
    db = FakeDB(hospital)
    body = hospitals_api.HospitalProfileUpdate(keywords=[])

    await hospitals_api.update_profile(hospital.id, body, BackgroundTasks(), db=db)

    assert hospital.profile_complete is False
    assert db.committed is True
    assert db.locks == [hospital.id]
    assert db.locked_before_first_read is True


async def test_a_legacy_gap_this_patch_did_not_create_keeps_the_live_hospital_saving():
    """저장 전에 이미 비어 있던 항목 때문에 무관한 칸 수정이 409가 되면 안 된다."""
    hospital = _hospital(
        status=HospitalStatus.ACTIVE,
        site_live=True,
        profile_complete=True,
        director_philosophy=None,
    )
    db = FakeDB(hospital)

    result = await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(phone="02-111-2222"),
        BackgroundTasks(),
        db=db,
    )

    # 완료는 그대로 두고, 남은 항목은 응답이 말한다.
    assert hospital.profile_complete is True
    assert result["profile_complete"] is True
    assert [item["key"] for item in result["missing_profile_requirements"]] == [
        "director_philosophy"
    ]
    assert db.committed is True


async def test_a_legacy_gap_does_not_flip_completion_off_for_a_non_live_hospital():
    hospital = _hospital(profile_complete=True, director_philosophy=None)
    db = FakeDB(hospital)

    result = await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(phone="02-111-2222"),
        BackgroundTasks(),
        db=db,
    )

    assert hospital.profile_complete is True
    assert result["missing_profile_requirements"] == [
        {"key": "director_philosophy", "label": "진료 철학"}
    ]


async def test_this_patch_emptying_a_requirement_flips_completion_off_when_not_live():
    hospital = _hospital(profile_complete=True)
    db = FakeDB(hospital)

    result = await hospitals_api.update_profile(
        hospital.id,
        hospitals_api.HospitalProfileUpdate(keywords=[]),
        BackgroundTasks(),
        db=db,
    )

    assert hospital.profile_complete is False
    assert result["profile_complete"] is False
