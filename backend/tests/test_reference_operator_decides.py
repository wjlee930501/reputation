"""진료비·병원 선택 글의 참고자료 — 수기 목록으로 채우지 않고, 보류는 사람이 정한다.

2026-09-29 김실장 결정. 비용·가격이나 병원·전문의·진료과 고르기를 다루는 글에는 그 주장을
뒷받침할 공신력 있는 문서가 본질적으로 없다. 그래서:

- 생성·발행의 수기 목록 치유를 하지 않는다(가짜 근거를 붙이지 않는다).
- 예정 글이 참고자료 0개로 막히면 자동 본문 수리·주제 교체가 아니라 곧바로
  `OPERATOR_REQUIRED`(사람의 결정)다.
- 이미 공개된 글의 참고자료·상태·판은 그대로다. 공개됐던 글을 다시 여는 restore만
  참고자료를 보고, 통과하지 못하면 409로 사람에게 알린다.
- 통과한 참고자료가 있는 진료비 글은 그대로 발행한다. 본문에서 비용을 말할 뿐인 의료 글은
  종전처럼 치유한다.

네트워크·DB는 쓰지 않는다 — 가짜 fetcher와 더블만 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from types import SimpleNamespace

import arrow
import pytest
from fastapi import HTTPException

from app.api.admin import content as content_api
from app.services import content_engine
from app.services.reference_publication import (
    apply_publication_reference_refresh,
    refresh_publication_references,
    verify_publication_references,
)
from app.services.reference_requirement import (
    NO_SOURCE_TOPIC_COST,
    NO_SOURCE_TOPIC_PROVIDER_CHOICE,
    references_left_to_operator,
    topic_without_authoritative_source,
)
from app.services.reference_verification import ReferenceVerifier, override_reference_fetcher
from app.workers import generation_incident_control, tasks, topic_swap_fallback
from app.workers.generation_incident_control import (
    REFERENCES_OPERATOR_DECIDES_ACTION,
    REFERENCES_OPERATOR_DECIDES_CAUSE,
    generation_block_is_terminal,
    scheduled_recovery_owns_blocker,
)
from app.workers.generation_retry_policy import (
    OPERATOR_DECIDES_KEY,
    GenerationRetryClass,
    retry_is_due,
)
from tests.test_generation_recovery_ladder import _FakeIncidentSession, _freeze, _kst
from tests.test_reference_publication_gate import (
    CURATED_HEMORRHOID_AMC,
    CURATED_HEMORRHOID_KDCA,
    GUESSED,
    HEMORRHOID_TITLE,
    _CommitDB,
    _gate_setup,
    _GateDB,
    _hemorrhoid_fetcher,
    _publish_setup,
)
from tests.test_tasks_nightly import _NightlyTaskDB, _publication_hospital, _publication_item

# 치유가 있었다면 수기 목록의 치핵 문서(5818·아산 31772)를 받는 제목들 — 그 문서가 비용·병원
# 고르기를 뒷받침하지 않는다는 것이 이 결정의 이유다.
COST_TITLE = "치질 수술 비용 — 보험 적용과 본인부담"
CHOICE_TITLE = "치질 수술 병원 추천 — 어느 병원에서 받아야 할까요"
CURATED_HEMORRHOID = {CURATED_HEMORRHOID_KDCA, CURATED_HEMORRHOID_AMC}
_SEVEN_FORTY_FIVE = arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")


# ── 분류: 제목만 본다 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "title, expected",
    [
        # 비용 — 보류 재생의 실제 제목(2026-09-29)
        ("경산 내과 진료비, 무엇에 따라 달라지나요 — 김효진 원장의 안내", NO_SOURCE_TOPIC_COST),
        ("도수치료 비용 — 시간·횟수·보험에 따라 어떻게 달라지나요?", NO_SOURCE_TOPIC_COST),
        ("PRP 자가혈 재생치료 비용 — 어떤 요인에 따라 달라지나요?", NO_SOURCE_TOPIC_COST),
        ("노원구 정형외과 진료비 — 건강보험 적용과 본인부담 구조", NO_SOURCE_TOPIC_COST),
        ("비급여 도수치료 가격 안내", NO_SOURCE_TOPIC_COST),
        # 병원 선택 — 대상(병원·전문의·진료과) 바로 뒤의 고르기 말
        ("경산 내과 병원 추천 — 증상별 진료 흐름과 판단 기준", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("노원구 마취통증의학과 전문의 추천은 어디인가요?", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("마포구 신경외과 선택 고민 — 진단 정확성을 우선하는 이유", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("간질환 치료 전문의 — 내과·소화기내과 진료과목 선택 기준", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("하남시 간질환 진료, 어느 병원으로 가야 할까", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("간질환 치료하려면 어떤 전문의를 찾아야 할까요?", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        # 떨어진 고르기 말은 같은 제목에 대상이 있을 때만
        ("경산 내과 병원, 검진부터 만성질환까지 어떻게 선택할까요?", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("경산 소아청소년 병원, 무엇을 기준으로 비교해야 할까요", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("경산 내과 병원 어디가 좋은가요? — 비교 기준 정리", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        # 의료 글 — 치료·수술 선택, 특정 시술이 '가능한 병원', 고르기 대상 없는 비교
        ("상계동 도수치료 가능한 병원 — 통증 원인 진단과 치료 선택 기준", None),
        ("도화동 도수치료 가능한 병원은 어디인가요?", None),
        ("하남시 위례 간질환 환자 진료 흐름 — 초음파·혈액검사·추적", None),
        ("무릎 인공관절 수술, 어떻게 선택할까요?", None),
        ("대장내시경 준비약 비교해야 할 점", None),
        (HEMORRHOID_TITLE, None),
    ],
)
def test_title_classification_is_deterministic_and_conservative(title, expected):
    assert topic_without_authoritative_source(title) == expected


def test_body_query_target_and_faq_question_never_reclassify_a_medical_title():
    """본문·측정 질문·FAQ 질문이 비용·병원 고르기를 말해도 제목이 의료 글이면 의료 글이다."""

    item = SimpleNamespace(
        content_type="TREATMENT",
        title="하남시 위례 간질환 환자 진료 흐름 — 초음파·혈액검사·추적",
        body="## 검사 비용\n검사 비용은 병원마다 다르며 진료비는 보험 적용에 따라 달라집니다.",
        faq_question=None,
        content_brief={
            "target_query": "간질환 치료 비용이 얼마나 드는지 알려줘",
            "query_target": {"name": "간질환 치료 비용이 얼마나 드는지 알려줘"},
        },
        query_target_id=uuid.uuid4(),
    )
    assert not references_left_to_operator(item)
    # FAQ 질문은 측정 질문을 그대로 옮기는 일이 많다 — 제목이 의료 글이면 의료 글이다.
    item.content_type = "FAQ"
    item.title = "하남시 당뇨 진료 병원 — 혈당 확인부터 합병증 검사까지"
    item.faq_question = "당뇨 진료를 받으려는데 하남시 어느 병원으로 가야 해?"
    assert not references_left_to_operator(item)
    item.title = "하남시 당뇨 진료, 어느 병원으로 가야 할까요?"
    assert references_left_to_operator(item)


def test_a_post_whose_references_are_not_required_is_never_left_to_the_operator():
    notice = SimpleNamespace(
        content_type="NOTICE", title="추석 연휴 진료비 수납 안내", faq_question=None,
        content_brief=None, query_target_id=None,
    )
    assert not references_left_to_operator(notice)


# ── (i)·(ii) 예정 글: 채우지 않고 곧바로 사람의 결정 ─────────────────────────


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_scheduled_post_is_held_for_the_operator_without_a_curated_fill(monkeypatch, title):
    item, _db, effects = _publish_setup(monkeypatch, references=[], checks=None, title=title)
    fetcher = _hemorrhoid_fetcher()
    _freeze(monkeypatch, _kst(2026, 6, 10, 8, 0))

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert payload["kind"] == "blocked"
    assert payload["code"] == "MISSING_REFERENCES"
    assert item.references_list == []  # 수기 목록으로 채우지 않는다
    assert fetcher.calls == []  # 치유 후보를 열지도 않는다
    assert item.status is tasks.ContentStatus.DRAFT
    assert effects == {"revalidate": [], "indexnow": []}
    # 발행기가 남긴 정본 시도 기록 — 사람의 결정이며 다음 자동 시도 시각이 없다.
    attempt = item.essence_check_summary["generation_attempt"]
    assert attempt["reason"] == "MISSING_REFERENCES"
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert attempt[OPERATOR_DECIDES_KEY] is True
    assert attempt["next_retry_at"] is None
    assert not retry_is_due(attempt)


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_seven_forty_five_removes_a_dead_reference_but_never_fills_it(monkeypatch, title):
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=title)
    item.references_list = [{"title": "추측 주소", "url": GUESSED}]
    item.reference_checks = None
    incidents = _gate_setup(monkeypatch, item)
    fetcher = _hemorrhoid_fetcher()

    with override_reference_fetcher(fetcher):
        paged = tasks._page_morning_stored_publication_gates(_GateDB(item), now_kst=_SEVEN_FORTY_FIVE)

    assert paged == 1
    assert incidents[0]["code"] == "MISSING_REFERENCES"
    assert item.references_list == []
    assert fetcher.calls == [GUESSED]  # 죽은 주소만 확인했고 수기 목록 후보는 열지 않았다


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
async def test_the_hold_is_an_open_operator_incident_with_its_own_copy(monkeypatch, title):
    item = _slot(title)
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        _NightlyTaskDB(), item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
    )

    assert not scheduled_recovery_owns_blocker("MISSING_REFERENCES", item)
    assert generation_block_is_terminal("MISSING_REFERENCES", item)
    incident, request = await _open_incident(monkeypatch, item)

    assert (incident.state, incident.sla_due_at) == ("OPEN", None)
    assert request.safe_error_message == REFERENCES_OPERATOR_DECIDES_CAUSE
    assert request.next_action == REFERENCES_OPERATOR_DECIDES_ACTION
    assert "다시 쓰지 않습니다" in request.next_action


async def test_an_ordinary_missing_reference_hold_keeps_its_repair_copy(monkeypatch):
    item = _slot(HEMORRHOID_TITLE)
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        _NightlyTaskDB(), item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
    )

    assert OPERATOR_DECIDES_KEY not in item.essence_check_summary["generation_attempt"]
    incident, request = await _open_incident(monkeypatch, item)

    assert incident.state == "RETRYING"  # 수리 세션 예산이 소유한다
    assert request.safe_error_message != REFERENCES_OPERATOR_DECIDES_CAUSE


def test_a_stored_record_from_before_the_rule_is_rewritten_as_operator_work(monkeypatch):
    """배포 전에 남은 수리 소유 기록(표시 없음)도 다음 게이트에서 사람의 결정으로 고친다."""

    item = _slot(COST_TITLE)
    item.essence_check_summary = {
        "generation_attempt": {
            "reason": "MISSING_REFERENCES",
            "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
            "next_retry_at": _kst(2026, 9, 16, 12, 0).astimezone(UTC).isoformat(),
        }
    }
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        _NightlyTaskDB(), item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
    )

    attempt = item.essence_check_summary["generation_attempt"]
    assert attempt[OPERATOR_DECIDES_KEY] is True
    assert attempt["next_retry_at"] is None


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_recovery_sweep_never_buys_a_body_repair_for_the_hold(monkeypatch, title):
    philosophy = SimpleNamespace(id=uuid.uuid4())
    item = SimpleNamespace(
        id=uuid.uuid4(), hospital_id=uuid.uuid4(), body="stored body", title=title,
        image_url=None, content_philosophy_id=philosophy.id, faq_question=None,
        content_type=SimpleNamespace(value="TREATMENT"), query_target_id=None,
        content_brief=None, essence_check_summary={},
    )
    hospital = SimpleNamespace(id=item.hospital_id, name="비용글의원")
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(tasks, "assess_content_publication", lambda *_args: SimpleNamespace(
        code="MISSING_REFERENCES", message="공신력 있는 참고 자료를 확보하지 못했습니다."))
    spent: list[str] = []
    monkeypatch.setattr(tasks, "_spend_body_repair_session", lambda *_a: spent.append("session"))
    monkeypatch.setattr(
        tasks.cost_guard,
        "check_and_increment",
        lambda *_args: pytest.fail("작가(LLM)를 부르면 안 된다"),
    )

    state, code, _message = tasks._generate_single_content_item(_NightlyTaskDB(), item, hospital)

    assert (state, code) == (tasks.GenerationItemState.FAILED, "MISSING_REFERENCES")
    assert spent == []


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_the_hold_never_becomes_a_topic_swap_candidate(monkeypatch, title):
    item = _slot(title)
    item.topic_swap_history = None
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        _NightlyTaskDB(), item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
    )

    assert topic_swap_fallback.exhausted_body_sample_reason(item) is None


# ── 생성 단계의 치유도 같다 ──────────────────────────────────────────────


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
async def test_generation_never_fills_the_post_from_the_curated_list(title):
    result = {
        "title": title,
        "body": "## 안내\n본문",
        "faq_question": None,
        "references": [{"title": "추측 주소", "url": GUESSED}],
    }
    fetcher = _hemorrhoid_fetcher()

    with override_reference_fetcher(fetcher):
        await content_engine._verify_generated_references(result, None, required=True)

    assert result["references"] == []
    assert fetcher.calls == [GUESSED]
    assert content_engine._topic_aligned_curated_sources(None, result) == []


async def test_generation_still_fills_an_ordinary_medical_post():
    result = {
        "title": HEMORRHOID_TITLE,
        "body": "## 회복 기간\n수술 비용은 병원마다 다를 수 있습니다.",
        "faq_question": None,
        "references": [{"title": "추측 주소", "url": GUESSED}],
    }
    with override_reference_fetcher(_hemorrhoid_fetcher()):
        await content_engine._verify_generated_references(result, None, required=True)

    assert {ref["url"] for ref in result["references"]} <= CURATED_HEMORRHOID
    assert result["references"]


# ── (iii) 공개된 글: 참고자료·상태·판 그대로, 알림은 restore 409 ────────────────


def _published(title, status):
    return SimpleNamespace(
        title=title,
        body="진료 기준을 안내합니다.",
        content_brief=None,
        faq_question=None,
        content_type="TREATMENT",
        content_revision=7,
        status=status,
        references_list=[{"title": "추측 주소", "url": GUESSED}],
        reference_checks=None,
    )


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
async def test_published_post_is_never_changed_and_restore_reports_it(title):
    references = [{"title": "추측 주소", "url": GUESSED}]

    # 재검증이 비우더라도(치유 없음) 공개 글에는 적용되지 않는다.
    item = _published(title, tasks.ContentStatus.PUBLISHED)
    fetcher = _hemorrhoid_fetcher()
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))
    assert refresh.operator_decides and not refresh.healed and refresh.references == []
    assert fetcher.calls == [GUESSED]
    assert not apply_publication_reference_refresh(item, refresh)
    assert (item.references_list, item.status, item.content_revision) == (
        references, tasks.ContentStatus.PUBLISHED, 7
    )

    # 공개됐던 글(비공개 보존)을 다시 여는 restore — 사람에게 409로 알리고 아무것도 채우지 않는다.
    withheld = _published(title, tasks.ContentStatus.WITHHELD)
    verification = await verify_publication_references(
        withheld, ReferenceVerifier(_hemorrhoid_fetcher(), domain_spacing=0)
    )
    with pytest.raises(HTTPException) as raised:
        await content_api._require_restorable_references(_CommitDB(), withheld, verification)
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "REFERENCES_NOT_VERIFIED"
    assert GUESSED in raised.value.detail["message"]
    assert (withheld.references_list, withheld.status, withheld.content_revision) == (
        references, tasks.ContentStatus.WITHHELD, 7
    )


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_seven_forty_five_never_touches_a_published_post(monkeypatch, title):
    """잠금 뒤 공개 상태면 아무것도 쓰지 않고 인시던트도 열지 않는다."""

    references = [{"title": "추측 주소", "url": GUESSED}]
    hospital = _publication_hospital()
    item = _publication_item(hospital, body="진료 기준을 안내합니다.", title=title)
    item.status = tasks.ContentStatus.PUBLISHED
    item.references_list = [dict(ref) for ref in references]
    item.reference_checks = None
    item.content_revision = 7
    incidents = _gate_setup(monkeypatch, item)

    with override_reference_fetcher(_hemorrhoid_fetcher()):
        tasks._page_morning_stored_publication_gates(_GateDB(item), now_kst=_SEVEN_FORTY_FIVE)

    assert (item.references_list, item.status, item.content_revision) == (
        references, tasks.ContentStatus.PUBLISHED, 7
    )
    assert item.reference_checks is None
    assert incidents == []


# ── (iv)·(v) 바뀌지 않는 것 ────────────────────────────────────────────────


def test_medical_post_mentioning_cost_in_its_body_still_heals(monkeypatch):
    item, _db, _effects = _publish_setup(
        monkeypatch, references=[{"title": "치질 수술 안내", "url": GUESSED}], checks=None
    )
    item.body = "## 회복 기간\n수술 비용과 진료비는 보험 적용에 따라 다릅니다."

    tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(_hemorrhoid_fetcher(), domain_spacing=0)
    )

    urls = {ref["url"] for ref in item.references_list}
    assert urls and urls <= CURATED_HEMORRHOID
    assert item.status is tasks.ContentStatus.PUBLISHED


def test_cost_post_with_a_passing_reference_keeps_it_and_publishes(monkeypatch):
    item, _db, _effects = _publish_setup(
        monkeypatch,
        references=[{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}],
        checks=None,
        title=COST_TITLE,
    )

    tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(_hemorrhoid_fetcher(), domain_spacing=0)
    )

    assert [ref["url"] for ref in item.references_list] == [CURATED_HEMORRHOID_KDCA]
    assert item.status is tasks.ContentStatus.PUBLISHED


# ── 도우미 ────────────────────────────────────────────────────────────────


def _slot(title):
    hospital_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=hospital_id,
        hospital=SimpleNamespace(id=hospital_id, name="사람결정의원"),
        scheduled_date=_kst(2026, 9, 16).date(),
        sequence_no=1,
        title=title,
        faq_question=None,
        content_type=SimpleNamespace(value="TREATMENT"),
        content_brief=None,
        query_target_id=None,
        essence_check_summary=None,
        topic_swap_history=None,
    )


async def _open_incident(monkeypatch, item):
    """`generation_incident_control.open_generation_incident`를 더블 세션으로 한 번 연다."""

    from datetime import datetime

    from app.models.operations import Incident, IncidentSeverity, IncidentState

    opened = Incident(
        id=uuid.uuid4(),
        severity=IncidentSeverity.HIGH,
        state=IncidentState.OPEN.value,
        customer_impact="",
        next_action="",
        admin_path="/operations",
        hospital_id=uuid.uuid4(),
        version=1,
        safe_error_code="MISSING_REFERENCES",
        safe_error_message="",
        episode_seq=1,
        sla_due_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    requests = []

    async def capture_request(_db, request, **_kwargs):
        requests.append(request)
        return opened

    async def retrying(_db, _incident_id, **_kwargs):
        opened.state = IncidentState.RETRYING.value
        return opened

    monkeypatch.setattr(
        generation_incident_control,
        "get_async_sessionmaker",
        lambda: lambda: _FakeIncidentSession(item),
    )
    monkeypatch.setattr(generation_incident_control, "open_or_touch_incident", capture_request)
    monkeypatch.setattr(generation_incident_control, "mark_retrying", retrying)
    await generation_incident_control.open_generation_incident(
        item_id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        hospital_name="사람결정의원",
        run_id=uuid.uuid4(),
        code="MISSING_REFERENCES",
        message="",
        notify=False,
    )
    return opened, requests[0]
