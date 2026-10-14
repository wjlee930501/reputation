"""완료 재판정 공용 서비스(P4-3) — 사람 PATCH와 자동 입력 적용이 같은 규칙을 쓴다."""

import uuid
from types import SimpleNamespace

from app.models.handoff import HandoffState
from app.services import profile_completion


def _hospital(**overrides):
    values = dict(
        id=uuid.uuid4(), profile_complete=False,
        director_name="김원장", director_career="외과 전문의", director_philosophy=None,
        address="서울", phone="02-1", business_hours={"mon": "09:00-18:00"},
        website_url="https://a.example", blog_url=None,
        naver_place_url="https://naver.me/x", google_maps_url=None,
        google_business_profile_url=None, latitude=37.5, longitude=127.0,
        region=["강남"], specialties=["외과"], keywords=["치질"],
        treatments=[{"name": "치질 수술"}],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _DB:
    def __init__(self, handoff):
        self.handoff = handoff

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.handoff)


def test_derived_completion_for_an_incomplete_profile() -> None:
    decision = profile_completion.decide_profile_completion(_hospital(), missing_before=set())
    assert decision.next_complete is True and decision.transitioned is True


def test_newly_emptied_requirement_demotes_but_not_in_promote_only_mode() -> None:
    hospital = _hospital(profile_complete=True, keywords=[])
    demote = profile_completion.decide_profile_completion(hospital, missing_before=set())
    assert demote.next_complete is False
    assert demote.newly_missing_requirements == ["targeting"]
    keep = profile_completion.decide_profile_completion(
        hospital, missing_before=set(), promote_only=True
    )
    assert keep.next_complete is True


def test_legacy_gap_keeps_completion() -> None:
    hospital = _hospital(profile_complete=True, keywords=[])
    decision = profile_completion.decide_profile_completion(hospital, missing_before={"targeting"})
    assert decision.next_complete is True and decision.transitioned is False


async def test_promotion_without_accepted_handoff_is_blocked_and_not_applied() -> None:
    hospital = _hospital()
    decision = await profile_completion.evaluate_profile_completion(
        _DB(SimpleNamespace(state=HandoffState.CONTRACT_PENDING)), hospital, missing_before=set()
    )
    assert decision.blocked and decision.blocker["code"] == "HANDOFF_NOT_ACCEPTED"
    assert decision.transitioned is False
    assert hospital.profile_complete is False


async def test_promotion_with_accepted_handoff_is_applied() -> None:
    hospital = _hospital()
    decision = await profile_completion.evaluate_profile_completion(
        _DB(SimpleNamespace(state=HandoffState.HANDOFF_ACCEPTED)), hospital, missing_before=set()
    )
    assert decision.transitioned is True
    assert hospital.profile_complete is True
    assert profile_completion.completion_audit_detail(decision)["profile_complete_transition"]


async def test_side_effects_dispatch_site_build_and_v0(monkeypatch) -> None:
    from app.workers import tasks

    sent, dispatched = [], []
    monkeypatch.setattr(tasks.build_aeo_site, "apply_async", lambda **kw: sent.append(kw))

    async def _dispatch(_db, command, _task):
        dispatched.append((command.operation_type, command.audit_actor))

    monkeypatch.setattr(profile_completion, "dispatch_operation", _dispatch)
    hospital_id = uuid.uuid4()

    await profile_completion.dispatch_completion_side_effects(
        None, hospital_id, actor="system:profile-autofill"
    )

    assert sent and sent[0]["args"] == [str(hospital_id)] and sent[0]["queue"] == "default"
    assert dispatched == [("TRIGGER_V0_REPORT", "system:profile-autofill")]
