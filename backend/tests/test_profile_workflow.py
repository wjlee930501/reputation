import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.admin import hospitals as hospitals_api
from app.models.handoff import HandoffState


def test_profile_completion_is_blocked_before_handoff_acceptance() -> None:
    hospital = SimpleNamespace(id=uuid.uuid4())
    handoff = SimpleNamespace(state=HandoffState.CONTRACT_PENDING)

    blocker = hospitals_api.profile_completion_handoff_blocker(hospital, handoff)

    assert blocker == {
        "code": "HANDOFF_NOT_ACCEPTED",
        "missing": ["handoff_accepted"],
        "message": "고객 인수 승인이 완료된 뒤 프로파일을 완료할 수 있습니다.",
    }


class PendingProfileDB:
    def __init__(self, hospital, handoff):
        self.hospital = hospital
        self.handoff = handoff
        self.committed = False

    async def get(self, _model, object_id):
        return self.hospital if object_id == self.hospital.id else None

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.handoff)

    def add(self, _item):
        return None

    async def commit(self):
        self.committed = True


async def test_complete_profile_returns_machine_blocker_and_enqueues_nothing() -> None:
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        name="QA",
        slug="qa",
        profile_complete=False,
        status="ONBOARDING",
        plan=None,
        source_lead_id=None,
        site_live=False,
        site_built=False,
        v0_report_done=False,
        schedule_set=False,
        created_at=None,
        address="서울",
        phone="02",
        business_hours={"mon": "09-18"},
        website_url="https://clinic.example",
        blog_url=None,
        kakao_channel_url=None,
        google_business_profile_url=None,
        google_maps_url="https://maps.example/x",
        naver_place_url="https://naver.me/example",
        latitude=37.5,
        longitude=127.0,
        wikidata_qid=None,
        gbp_place_id=None,
        naver_place_id=None,
        kakao_place_id=None,
        hira_org_id=None,
        region=["서울"],
        specialties=["외과"],
        keywords=["진료"],
        competitors=[],
        director_name="김원장",
        director_career="전문의",
        director_philosophy="충분히 설명합니다.",
        director_credentials=None,
        treatments=[{"name": "진료"}],
        aeo_domain=None,
    )
    handoff = SimpleNamespace(state=HandoffState.CONTRACT_PENDING)
    tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        await hospitals_api.update_profile(
            hospital.id,
            hospitals_api.HospitalProfileUpdate(profile_complete=True),
            tasks,
            db=PendingProfileDB(hospital, handoff),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "HANDOFF_NOT_ACCEPTED"
    assert tasks.tasks == []
