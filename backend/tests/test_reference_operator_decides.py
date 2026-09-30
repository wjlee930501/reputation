"""진료비·병원 선택 글의 참고자료 — 수기 목록으로 채우지 않고, 보류는 사람이 정한다.

2026-09-29 김실장 결정. 비용·가격이나 병원·전문의·진료과 고르기를 다루는 글에는 그 주장을
뒷받침할 공신력 있는 문서가 본질적으로 없다. 그래서:

- 생성·발행의 수기 목록 치유를 하지 않는다(가짜 근거를 붙이지 않는다).
- 예정 글이 참고자료 0개로 막히면 자동 본문 수리·주제 교체가 아니라 곧바로
  `OPERATOR_REQUIRED`(사람의 결정)다.
- 이미 공개된 글의 참고자료·상태·판은 그대로다. 공개됐던 글을 다시 여는 restore만
  참고자료를 보고, 통과하지 못하면 409로 사람에게 알린다.
- 발행 전 글(예정 DRAFT·READY, 쓰이지 않은 슬롯의 생성)은 작가가 인용해 통과한 수기 목록
  문서도 남기지 않는다 — 그 통과는 GET이 아니라 치유와 같은 카탈로그 키워드 대조다(2026-09-29
  실장 결정). 목록 밖 문서가 실제 GET으로 통과했으면 그대로 발행한다. 본문에서 비용을 말할
  뿐인 의료 글은 종전처럼 치유한다.

네트워크·DB는 쓰지 않는다 — 가짜 fetcher와 더블만 쓴다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import UTC, timedelta
from types import SimpleNamespace

import arrow
import pytest
from fastapi import HTTPException

from app.api.admin import content as content_api
from app.models.content import ContentType
from app.services import content_engine, reference_publication, reference_requirement
from app.services.reference_publication import (
    apply_publication_reference_refresh,
    publication_references_settled,
    refresh_publication_references,
    verify_publication_references,
)
from app.services.reference_requirement import (
    NO_SOURCE_TOPIC_COST,
    NO_SOURCE_TOPIC_PROVIDER_CHOICE,
    references_left_to_operator,
    title_names_medical_subject,
    topic_without_authoritative_source,
)
from app.services.reference_verification import (
    ReferenceVerifier,
    article_topic_terms,
    curated_sources_for_topic,
    curated_topic_relevant,
    override_reference_fetcher,
)
from app.utils.authority_sources import (
    CURATED_MEDICAL_SOURCE_PAGES,
    CURATED_SOURCE_URLS,
    keyword_names_provider,
    select_curated_authority_sources,
)
from app.workers import generation_incident_control, tasks, topic_swap_fallback
from app.workers.generation_incident_control import (
    REFERENCES_OPERATOR_DECIDES_ACTION,
    REFERENCES_OPERATOR_DECIDES_CAUSE,
    REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION,
    generation_block_is_terminal,
    operator_decides_references,
    scheduled_recovery_owns_blocker,
)
from app.workers.generation_retry_policy import (
    OPERATOR_DECIDES_KEY,
    GenerationRetryClass,
    retry_is_due,
)
from tests import test_topic_swap_fallback as swap_tests
from tests.reference_fetch_doubles import PageFetcher
from tests.test_content_compliance_faq import _content_item, _hospital, _PatchDB, _wire
from tests.test_generation_recovery_ladder import _FakeIncidentSession, _freeze, _kst
from tests.test_reference_publication_gate import (
    CURATED_HEMORRHOID_AMC,
    CURATED_HEMORRHOID_KDCA,
    GUESSED,
    HEMORRHOID_TITLE,
    KDCA_VIEW,
    _CommitDB,
    _gate_setup,
    _GateDB,
    _hemorrhoid_fetcher,
    _MutatingFetcher,
    _pass,
    _publish_setup,
    _stamp,
)
from tests.test_reference_requirement import (
    CURATED_HYPERTENSION,
    _brief_hypertension,
    _mapo_hospital,
    _notice_payload,
    _stub_writer,
)
from tests.test_tasks_nightly import _NightlyTaskDB, _publication_hospital, _publication_item

# 치유가 있었다면 수기 목록 문서를 받는 제목들 — 그 문서가 비용·병원 고르기를 뒷받침하지 않는다는
# 것이 이 결정의 이유다. 비용 글은 치핵 문서(5818·아산 31772)를, 병원 선택 글은 '병원선택' 경로
# 키워드로 정형외과 FAQ 문서(요통 3796·무릎 5969·디스크 3348)를 받았다. 병원 선택 글은 의료
# 주제가 없어야 한다 — '치질 수술 병원 추천'은 치핵 문서를 받는 의료 글이다(2026-09-29 팀장 결정).
COST_TITLE = "치질 수술 비용 — 보험 적용과 본인부담"
CHOICE_TITLE = "대장항문외과 병원 추천 — 병원 선택 기준과 진료 흐름"
CURATED_HEMORRHOID = {CURATED_HEMORRHOID_KDCA, CURATED_HEMORRHOID_AMC}
# 요통 문서. 진료과 이름·'병원선택' 경로 키워드는 주제가 아니라(`keyword_names_provider`) 병원
# 선택 제목만으로는 수기 목록 대조를 통과하지 않는다 — 본문 첫 H2가 허리통증을 말해야 통과한다.
CURATED_LOW_BACK = KDCA_VIEW.format(3796)
CHOICE_BODY = "## 허리통증이 오래갈 때 먼저 확인할 점\n진료 기준과 내원 시점을 안내합니다."
# 목록 밖 문서 — 실제 GET(제목·본문이 글 주제와 일치)으로만 통과한다.
UNLISTED_HEMORRHOID = KDCA_VIEW.format(9001)
# 글 제목 → 작가가 인용했고 수기 목록 대조를 통과하는 수기 목록 문서.
CITED_CURATED = {
    COST_TITLE: (CURATED_HEMORRHOID_KDCA, "치핵"),
    CHOICE_TITLE: (CURATED_LOW_BACK, "요통"),
}
# 그 통과의 근거가 되는 본문 — 비용 글은 제목의 '치질'로, 병원 선택 글은 첫 H2의 '허리통증'으로.
CITED_BODY = {
    COST_TITLE: "진료 기준과 내원 시점을 안내합니다.",
    CHOICE_TITLE: CHOICE_BODY,
}
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


@pytest.mark.parametrize(
    "title, expected",
    [
        # 의료 주제(수기 목록이 문서를 가진 질환·시술)가 있으면 고르기 말이 있어도 의료 글이다.
        ("경산 경동맥초음파 검사, 어느 병원에서 받아야 할까요?", None),
        ("하남시 간질환 진료, 어느 병원으로 가야 할까", None),
        ("간질환 치료 전문의 — 내과·소화기내과 진료과목 선택 기준", None),
        ("간질환 치료하려면 어떤 전문의를 찾아야 할까요?", None),
        ("마포구 신경외과 전문의 추천, 허리·다리 저림이면 어떻게 골라야 할까요?", None),  # 5d8534e7
        ("고지혈증 진료, 창원시 마산회원구에서 어느 병원으로 가야 할까?", None),  # ad1a8a8d
        ("경산 소화기내과, 검진부터 용종 제거까지 어떻게 선택할까요?", None),  # 2af61d33
        ("치질 수술 병원 추천 — 어느 병원에서 받아야 할까요", None),
        # 의료 주제가 없는 고르기 글은 그대로 병원 선택 글이다.
        ("경산 내과 병원 추천 — 증상별 진료 흐름과 판단 기준", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("일반의원 전문의, 어떤 기준으로 선택해야 할까요?", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        (
            "대장항문외과 선택 기준 — 정확한 진단과 맞춤형 치료를 위한 확인 포인트",
            NO_SOURCE_TOPIC_PROVIDER_CHOICE,
        ),
        ("경산 내과 병원, 검진부터 만성질환까지 어떻게 선택할까요?", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        # 수기 목록의 '병원선택'(정형외과 FAQ 경로)·'정형외과'(진료과 이름) 키워드는 의료 주제가
        # 아니다 — 이 제목들은 예전에 요통·무릎·디스크 문서를 가짜 근거로 받았다.
        ("경산 내과 병원 선택 기준 — 검진과 만성질환 관리", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        (
            "마포구 정형외과 병원 추천해줘 — 허리·무릎·어깨 통증 선택 기준",
            NO_SOURCE_TOPIC_PROVIDER_CHOICE,
        ),
        ("노원구 병원 추천 — 통증 종류별 진료 안내", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        ("하남시 심장내과 병원 추천 — 진료 과목과 선택 기준", NO_SOURCE_TOPIC_PROVIDER_CHOICE),
        # 진료비 규칙은 의료 주제 예외가 없다.
        ("갑상선결절 치료 비용 — 초음파·세침검사 건강보험 적용 기준", NO_SOURCE_TOPIC_COST),
    ],
)
def test_provider_choice_is_only_a_choice_post_without_a_medical_subject(title, expected):
    assert topic_without_authoritative_source(title) == expected
    if expected != NO_SOURCE_TOPIC_COST:
        assert title_names_medical_subject(title) is (expected is None)


def test_an_excluded_document_does_not_make_a_medical_subject(monkeypatch):
    """제외 목록의 문서는 붙일 수 없다 — 그 문서의 키워드만 겹치는 고르기 글은 병원 선택 글이다."""

    title = "경산 경동맥초음파 검사, 어느 병원에서 받아야 할까요?"
    assert topic_without_authoritative_source(title) is None
    carotid = {
        str(source["url"])
        for source in CURATED_MEDICAL_SOURCE_PAGES
        if "경동맥초음파" in source["keywords"]
    }
    assert carotid
    monkeypatch.setattr(
        reference_requirement,
        "reference_exclusion_reason",
        lambda url: "사람이 근거 불가로 확인" if url in carotid else None,
    )

    assert topic_without_authoritative_source(title) == NO_SOURCE_TOPIC_PROVIDER_CHOICE


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
    item.title = "하남시 내과 진료, 어느 병원으로 가야 할까요?"
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


@pytest.mark.parametrize("written", [True, False], ids=["written", "unwritten"])
@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
async def test_the_hold_is_an_open_operator_incident_with_its_own_copy(
    monkeypatch, title, written
):
    item = _slot(title)
    item.body = "이미 쓴 본문입니다." if written else None
    _freeze(monkeypatch, _kst(2026, 9, 16, 7, 45))
    tasks._record_gate_blocker_decision(
        _NightlyTaskDB(), item, SimpleNamespace(id="p1"), "MISSING_REFERENCES"
    )

    assert not scheduled_recovery_owns_blocker("MISSING_REFERENCES", item)
    assert generation_block_is_terminal("MISSING_REFERENCES", item)
    incident, request = await _open_incident(monkeypatch, item)

    assert (incident.state, incident.sla_due_at) == ("OPEN", None)
    assert request.safe_error_message == REFERENCES_OPERATOR_DECIDES_CAUSE
    if written:
        assert request.next_action == REFERENCES_OPERATOR_DECIDES_ACTION
        assert "다시 쓰지 않습니다" in request.next_action
    else:
        # 원고가 없는 글은 '새로 쓰기' 조치다(#187 2차 C).
        assert request.next_action == REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION


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


def test_cost_post_with_a_passing_unlisted_reference_keeps_it_and_publishes(monkeypatch):
    """목록 밖 문서가 실제 GET으로 통과했으면 남는다 — 같이 인용한 수기 목록 문서만 빠진다."""

    item, _db, _effects = _publish_setup(
        monkeypatch,
        references=[
            {"title": "치핵", "url": CURATED_HEMORRHOID_KDCA},
            {"title": "치질", "url": UNLISTED_HEMORRHOID},
        ],
        checks=None,
        title=COST_TITLE,
    )
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(UNLISTED_HEMORRHOID, "치질 | 국가건강정보포털 | 질병관리청", topic="치질")

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert item.references_list == [{"title": "치질", "url": UNLISTED_HEMORRHOID}]
    assert fetcher.calls == [UNLISTED_HEMORRHOID]  # 수기 목록 문서는 열지도 않고 뺐다
    assert item.status is tasks.ContentStatus.PUBLISHED


# ── (vi) 작가가 인용해 통과한 수기 목록 문서도 발행 전 진료비·병원 선택 글에는 남지 않는다 ──────
#
# 2026-09-29 실장 결정. 수기 목록 문서의 통과는 GET으로 본문을 확인한 것이 아니라 치유와 같은
# 카탈로그 키워드 대조다('도수치료 비용' 글이 프롬프트 힌트의 요통 문서를 인용해 통과했다).
# 각 테스트는 규칙을 끈 대조군으로 "그 문서가 실제로 통과한다"는 전제를 함께 확인한다.


def _curated_rule_off(monkeypatch):
    monkeypatch.setattr(
        reference_publication, "names_curated_document", lambda _entry, _checks=None: False
    )
    monkeypatch.setattr(content_engine, "is_curated_source_url", lambda _url: False)
    monkeypatch.setattr(
        content_engine, "names_curated_document", lambda _entry, _checks=None: False
    )


def _cited_curated_setup(monkeypatch, title, *, fresh):
    url, topic = CITED_CURATED[title]
    assert url in CURATED_SOURCE_URLS
    item, _db, effects = _publish_setup(
        monkeypatch,
        references=[{"title": topic, "url": url}],
        checks=[_pass(url)] if fresh else None,
        title=title,
    )
    item.body = CITED_BODY[title]
    _stamp(item)
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
    return item, effects, fetcher, url


@pytest.mark.parametrize("fresh", [False, True], ids=["unchecked", "fresh_pass"])
@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_scheduled_post_drops_a_cited_curated_document_even_when_it_passes(
    monkeypatch, title, fresh
):
    item, effects, fetcher, _url = _cited_curated_setup(monkeypatch, title, fresh=fresh)
    _freeze(monkeypatch, _kst(2026, 6, 10, 8, 0))

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert payload["kind"] == "blocked"
    assert payload["code"] == "MISSING_REFERENCES"
    assert item.references_list == []  # 빼고, 수기 목록으로 다시 채우지도 않는다
    assert fetcher.calls == []  # 뺄 문서도, 치유 후보도 열지 않는다
    assert item.status is tasks.ContentStatus.DRAFT
    assert effects == {"revalidate": [], "indexnow": []}
    attempt = item.essence_check_summary["generation_attempt"]
    assert attempt["reason"] == "MISSING_REFERENCES"
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert attempt[OPERATOR_DECIDES_KEY] is True
    assert attempt["next_retry_at"] is None


@pytest.mark.parametrize("fresh", [False, True], ids=["unchecked", "fresh_pass"])
@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_the_cited_curated_document_would_pass_without_the_rule(monkeypatch, title, fresh):
    """대조군 — 규칙을 끄면 같은 문서가 통과해 그대로 발행된다(위 테스트의 전제)."""

    item, _effects, fetcher, url = _cited_curated_setup(monkeypatch, title, fresh=fresh)
    _curated_rule_off(monkeypatch)
    _freeze(monkeypatch, _kst(2026, 6, 10, 8, 0))

    tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0))

    assert [ref["url"] for ref in item.references_list] == [url]
    assert item.status is tasks.ContentStatus.PUBLISHED


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
async def test_publication_refresh_drops_the_cited_curated_document_without_a_heal(title):
    url, topic = CITED_CURATED[title]
    item = _published(title, tasks.ContentStatus.DRAFT)
    item.references_list = [{"title": topic, "url": url}]
    fetcher = _hemorrhoid_fetcher()

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.references == [] and refresh.references_changed
    assert refresh.operator_decides and not refresh.healed and not refresh.deferred
    assert fetcher.calls == []
    assert apply_publication_reference_refresh(item, refresh)
    assert (item.references_list, item.content_revision) == ([], 8)


@pytest.mark.parametrize(
    "spelling",
    [
        CURATED_HEMORRHOID_KDCA.replace("https://", "http://"),
        CURATED_HEMORRHOID_KDCA.replace("https://", "https://www."),
        CURATED_HEMORRHOID_AMC.replace("https://www.", "https://"),
    ],
    ids=["http", "www", "no_www"],
)
async def test_a_curated_document_is_dropped_in_any_spelling(spelling):
    """표기 차이(scheme·www)는 같은 수기 목록 문서다 — 목록 밖 URL로 GET해 남기지 않는다."""

    assert spelling not in CURATED_SOURCE_URLS
    item = _published(COST_TITLE, tasks.ContentStatus.READY)
    item.references_list = [{"title": "치핵", "url": spelling}]
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(spelling, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.references == [] and refresh.operator_decides
    assert fetcher.calls == []


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
async def test_published_post_with_a_curated_document_stays_byte_identical(monkeypatch, title):
    """공개된 글은 규칙 밖이다 — 신선한 통과든 오래된 기록이든 무엇도 바뀌지 않는다."""

    url, topic = CITED_CURATED[title]
    item = _published(title, tasks.ContentStatus.PUBLISHED)
    item.body = CITED_BODY[title]
    item.references_list = [{"title": topic, "url": url}]
    item.reference_checks = [_pass(url)]
    _stamp(item)
    before = json.dumps(vars(copy.deepcopy(item)), default=str, sort_keys=True)
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)

    assert publication_references_settled(item)  # 발행 경로가 재검증하지 않는다
    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))
    assert refresh.already_current and refresh.references == item.references_list
    assert not refresh.references_changed and not refresh.operator_decides
    assert fetcher.calls == []
    assert json.dumps(vars(item), default=str, sort_keys=True) == before

    # 기록이 없어도(재검증은 돈다) 수기 목록 문서를 빼는 결과를 만들지 않는다.
    item.reference_checks = None
    stale = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))
    assert stale.references == item.references_list and not stale.references_changed


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_published_cost_post_with_a_curated_document_is_untouched_by_both_gates(
    monkeypatch, title
):
    url, topic = CITED_CURATED[title]
    item, _db, effects = _publish_setup(
        monkeypatch, references=[{"title": topic, "url": url}], checks=None, title=title
    )
    item.status = tasks.ContentStatus.PUBLISHED
    item.content_revision = 7
    incidents = _gate_setup(monkeypatch, item)
    before = json.dumps(vars(copy.deepcopy(item)), default=str, sort_keys=True)
    fetcher = _hemorrhoid_fetcher()

    assert tasks._auto_publish_one(item.id, reference_verifier=ReferenceVerifier(fetcher)) is None
    with override_reference_fetcher(fetcher):
        tasks._page_morning_stored_publication_gates(_GateDB(item), now_kst=_SEVEN_FORTY_FIVE)

    assert json.dumps(vars(item), default=str, sort_keys=True) == before
    assert incidents == [] and effects == {"revalidate": [], "indexnow": []}


# 생성 경로의 제목 — 고혈압 브리프(의료 주제 NOTICE, 참고자료 필수)에 맞춘다.
GENERATION_COST_TITLE = "마포 고혈압 진료비 — 검사·약값과 건강보험 본인부담"
GENERATION_CHOICE_TITLE = "마포 내과 병원 추천 — 병원 선택 기준과 진료 흐름"


def _curated_generation(monkeypatch, title):
    """작가가 수기 목록 문서 하나만 인용한 응답(그 문서는 수기 목록 대조를 통과한다).

    고혈압 문서는 브리프의 측정 키워드('고혈압')로 통과한다 — 병원 선택 제목 자체는 의료 주제가
    없어 어떤 수기 목록 문서도 제목으로는 통과하지 않는다.
    """

    url, topic = CURATED_HYPERTENSION, "고혈압"
    payload = _notice_payload([{"title": topic, "url": url}])
    payload["title"] = title
    calls = _stub_writer(monkeypatch, payload)
    fetcher = PageFetcher()
    fetcher.add_document(url, f"{topic} | 국가건강정보포털 | 질병관리청", topic=topic)
    return calls, fetcher, url


@pytest.mark.parametrize(
    "title", [GENERATION_COST_TITLE, GENERATION_CHOICE_TITLE], ids=["cost", "provider_choice"]
)
async def test_generation_does_not_accept_a_cited_curated_document(monkeypatch, title):
    _calls, fetcher, url = _curated_generation(monkeypatch, title)

    with override_reference_fetcher(fetcher):
        with pytest.raises(content_engine.MissingCitableReferencesError) as raised:
            await content_engine.generate_content(
                _mapo_hospital(), ContentType.NOTICE, content_brief=_brief_hypertension()
            )

    assert raised.value.result["references"] == []
    assert fetcher.calls == []  # 인용한 수기 목록 문서도, 치유 후보도 열지 않는다
    assert "수기 목록 문서를 쓰지 않음" in str(raised.value)  # 작가에게 이유가 간다
    # 쓰이지 않은 슬롯이면 이 거절이 곧바로 사람의 결정이 된다(작가가 만든 제목으로 판정).
    slot = _unwritten_slot()
    assert tasks._generation_left_references_to_operator(raised.value, slot)


@pytest.mark.parametrize(
    "title", [GENERATION_COST_TITLE, GENERATION_CHOICE_TITLE], ids=["cost", "provider_choice"]
)
async def test_generation_would_keep_the_cited_curated_document_without_the_rule(
    monkeypatch, title
):
    """대조군 — 규칙을 끄면 그 문서가 검증을 통과해 저장된다(위 테스트의 전제)."""

    _calls, fetcher, url = _curated_generation(monkeypatch, title)
    _curated_rule_off(monkeypatch)

    with override_reference_fetcher(fetcher):
        result = await content_engine.generate_content(
            _mapo_hospital(), ContentType.NOTICE, content_brief=_brief_hypertension()
        )

    assert [ref["url"] for ref in result["references"]] == [url]
    assert result["reference_checks"][-1]["reason"] == "curated_verified"


@pytest.mark.parametrize(
    "title", [GENERATION_COST_TITLE, GENERATION_CHOICE_TITLE], ids=["cost", "provider_choice"]
)
def test_unwritten_slot_whose_writer_cited_a_curated_document_goes_to_the_operator(
    monkeypatch, title
):
    """생성 경로 끝까지: 작가가 인용한 수기 목록 문서는 저장되지 않고 슬롯은 사람의 결정이 된다."""

    _calls, fetcher, _url = _curated_generation(monkeypatch, title)
    with override_reference_fetcher(fetcher):
        with pytest.raises(content_engine.MissingCitableReferencesError) as raised:
            asyncio.run(
                content_engine.generate_content(
                    _mapo_hospital(), ContentType.NOTICE, content_brief=_brief_hypertension()
                )
            )
    slot = _unwritten_slot()
    philosophy, writer_calls, incidents = _refless_generation(monkeypatch, slot, title)

    async def real_rejection(*, item, **_kwargs):
        writer_calls.append(item.id)
        raise raised.value

    monkeypatch.setattr(tasks, "_generate_with_auto_review", real_rejection)
    state, code, message = swap_tests._generate_once(monkeypatch, slot, _LADDER_SWEEPS[0])

    assert (state, code, message) == (
        tasks.GenerationItemState.FAILED,
        "MISSING_REFERENCES",
        REFERENCES_OPERATOR_DECIDES_CAUSE,
    )
    assert slot.body is None and slot.title is None
    assert not getattr(slot, "references_list", None)  # 수기 목록 문서를 저장하지 않았다
    attempt = _attempt_of(slot)
    assert attempt[OPERATOR_DECIDES_KEY] is True and attempt["next_retry_at"] is None
    assert [call["code"] for call in incidents] == ["MISSING_REFERENCES"]


# ── 쓰이지 않은 슬롯: 생성이 통과한 참고자료를 못 만들면 곧바로 사람의 결정 ──────────────
#
# 작가 회차를 다 쓰고도 참고자료가 0개(`MissingCitableReferencesError`)인 진료비·병원 선택
# 슬롯은 표본 사다리(GENERATION_REJECTED → 3일 소진 → 주제 교체)를 타지 않는다. 판정은 작가가
# 만든 제목으로 한다 — 행에는 아직 제목이 없다.

_SLOT_DAY = swap_tests.SLOT  # 2026-09-16
# 예정일 나흘 전 01시 복구 스윕부터 예정일 07시까지 — 의료 슬롯이면 표본 예산 3일이 소진된다.
_LADDER_SWEEPS = [
    swap_tests._kst(2026, 9, day, hour)
    for day in range(12, 17)
    for hour in (1, 4, 7, 23)
    if not (day == 16 and hour == 23)
]


def _refless_generation(monkeypatch, slot, title):
    """작가가 매번 `title`로 쓰지만 통과한 참고자료가 없어 GEO 게이트가 거절하는 생성 경로."""

    philosophy = swap_tests._approved_philosophy()
    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    swap_tests._patch_generation(monkeypatch, philosophy, slot, fail=True)
    writer_calls: list = []
    incidents: list[dict] = []

    async def refless_writer(*, item, **_kwargs):
        writer_calls.append(item.id)
        raise content_engine.MissingCitableReferencesError(
            "GEO hard-fail: references is empty for FAQ — 검증을 통과한 학회/KDCA 출처 없음.",
            {"title": title, "body": "## 안내\n본문", "references": []},
        )

    async def capture_incident(**kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(tasks, "_generate_with_auto_review", refless_writer)
    monkeypatch.setattr(tasks, "open_generation_incident", capture_incident)
    return philosophy, writer_calls, incidents


def _unwritten_slot():
    slot = swap_tests._swapped_slot(swap_tests._approved_philosophy())
    slot.topic_swap_history = None  # 주제 교체를 한 번도 하지 않은 슬롯
    return slot


def _attempt_of(slot) -> dict:
    return slot.essence_check_summary["generation_attempt"]


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_unwritten_slot_without_references_goes_straight_to_the_operator(monkeypatch, title):
    slot = _unwritten_slot()
    philosophy, writer_calls, incidents = _refless_generation(monkeypatch, slot, title)

    assert swap_tests._claims_at(monkeypatch, slot, _LADDER_SWEEPS[0])
    state, code, message = swap_tests._generate_once(monkeypatch, slot, _LADDER_SWEEPS[0])

    assert (state, code, message) == (
        tasks.GenerationItemState.FAILED,
        "MISSING_REFERENCES",
        REFERENCES_OPERATOR_DECIDES_CAUSE,
    )
    assert slot.body is None and slot.title is None  # 본문은 저장하지 않는다
    attempt = _attempt_of(slot)
    assert attempt["reason"] == "MISSING_REFERENCES"  # 새 사유 코드는 없다
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert attempt[OPERATOR_DECIDES_KEY] is True
    assert attempt["next_retry_at"] is None
    assert attempt["exhausted_days"] == 0 and attempt["provider_attempt_count"] == 0
    assert not retry_is_due(attempt)
    assert [(call["code"], call["message"]) for call in incidents] == [
        ("MISSING_REFERENCES", REFERENCES_OPERATOR_DECIDES_CAUSE)
    ]

    # 이후 어느 스윕도 이 슬롯을 다시 집지 않고(작가·본문 수리 0회), 주제 교체 후보도 아니다.
    for moment in _LADDER_SWEEPS[1:]:
        assert not swap_tests._claims_at(monkeypatch, slot, moment), moment
        report = topic_swap_fallback.swap_exhausted_topics(
            swap_tests._FakeDB([slot]),
            window_start=_SLOT_DAY - timedelta(days=7),
            window_end=_SLOT_DAY + timedelta(days=2),
            now=moment,
        )
        assert (report.considered, report.swapped) == (0, 0), moment
    # 로더를 우회해 워커를 직접 불러도 작가를 다시 사지 않는다.
    state, code, _message = swap_tests._generate_once(monkeypatch, slot, _LADDER_SWEEPS[-1])
    assert (state, code) == (tasks.GenerationItemState.SKIPPED, "MISSING_REFERENCES")
    assert writer_calls == [slot.id]
    assert _attempt_of(slot) == attempt
    assert topic_swap_fallback.exhausted_body_sample_reason(slot) is None


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_the_morning_gate_keeps_the_generation_hold_as_operator_work(monkeypatch, title):
    """07:45·08:00이 보는 증상(CONTENT_NOT_GENERATED)이 사람의 결정 기록을 덮어쓰지 않는다."""

    slot = _unwritten_slot()
    philosophy, writer_calls, _incidents = _refless_generation(monkeypatch, slot, title)
    swap_tests._generate_once(monkeypatch, slot, _LADDER_SWEEPS[0])
    attempt = _attempt_of(slot)

    swap_tests._freeze(monkeypatch, swap_tests._kst(2026, 9, 16, 7, 45))
    code, message = tasks._publication_block_details(
        slot, SimpleNamespace(code="CONTENT_NOT_GENERATED", message="본문이 아직 없습니다.")
    )
    tasks._record_gate_blocker_decision(_NightlyTaskDB(), slot, philosophy, code)

    assert (code, message) == ("MISSING_REFERENCES", REFERENCES_OPERATOR_DECIDES_CAUSE)
    assert _attempt_of(slot) == attempt
    assert not swap_tests._claims_at(monkeypatch, slot, swap_tests._kst(2026, 9, 17, 1, 0))
    assert writer_calls == [slot.id]


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_the_generation_hold_is_an_open_incident_without_a_deadline(monkeypatch, title):
    slot = _unwritten_slot()
    _refless_generation(monkeypatch, slot, title)
    swap_tests._generate_once(monkeypatch, slot, _LADDER_SWEEPS[0])  # 워커는 동기 경로다

    assert not scheduled_recovery_owns_blocker("MISSING_REFERENCES", slot)
    assert generation_block_is_terminal("MISSING_REFERENCES", slot)
    incident, request = asyncio.run(_open_incident(monkeypatch, slot))

    assert (incident.state, incident.sla_due_at) == ("OPEN", None)
    assert request.safe_error_message == REFERENCES_OPERATOR_DECIDES_CAUSE
    # 작가 회차가 참고 자료 0개로 끝나 원고가 저장되지 않았다 — 원고 없는 글의 조치다(#187 2차 C).
    assert not (getattr(slot, "body", None) or "").strip()
    assert request.next_action == REFERENCES_OPERATOR_DECIDES_UNWRITTEN_ACTION


@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_generation_heal_never_fills_a_cost_or_choice_slot(monkeypatch, title):
    # 채웠다면 검증기를 거쳐 결과를 돌려줬을 것이다 — 채움 여부만 보려고 검증기를 통과시킨다.
    monkeypatch.setattr(content_engine, "_validate_generated_result", lambda result, *_a: result)
    error = content_engine.MissingCitableReferencesError(
        "GEO hard-fail: references is empty for FAQ",
        {"title": title, "body": "## 안내\n본문", "faq_question": None, "references": []},
    )

    assert content_engine._heal_from_curated_catalog(error, None, None, None) is None
    assert error.result["references"] == []


def test_an_ordinary_unwritten_slot_keeps_the_sample_ladder_and_its_topic_swap(monkeypatch):
    """의료 글은 종전 그대로 — GENERATION_REJECTED 표본 사다리, 3일 소진 뒤 주제 교체 후보."""

    slot = _unwritten_slot()
    _philosophy, writer_calls, incidents = _refless_generation(monkeypatch, slot, HEMORRHOID_TITLE)

    first = None
    for moment in _LADDER_SWEEPS:
        if swap_tests._claims_at(monkeypatch, slot, moment):
            swap_tests._generate_once(monkeypatch, slot, moment)
            first = first or dict(_attempt_of(slot))

    assert first["reason"] == "GENERATION_REJECTED"
    assert first["retry_class"] == GenerationRetryClass.SAMPLE_RECOVERABLE.value
    assert OPERATOR_DECIDES_KEY not in first
    attempt = _attempt_of(slot)
    assert attempt["reason"] == "GENERATION_REJECTED"
    assert attempt["retry_class"] == GenerationRetryClass.OPERATOR_REQUIRED.value
    assert attempt["exhausted_days"] >= 3
    assert len(writer_calls) > 1  # 사다리가 작가를 다시 샀다
    assert {call["code"] for call in incidents} == {"GENERATION_REJECTED"}
    assert topic_swap_fallback.exhausted_body_sample_reason(slot) == "GENERATION_REJECTED"


# ── (vii) 2차 리뷰(4dc64119 BLOCK) — 가짜 출처가 남는 세 경로 ──────────────────────────
#
# (a) 생성 프롬프트의 '검증된 문서' 힌트는 브리프의 측정 질문이 진료비·병원 선택이면 비운다.
# (b) 진료과 이름·병원 고르기 경로 키워드(정형외과·병원선택·통증종류 …)만 겹쳐서는 수기 목록
#     문서가 통과하지도(`curated_topic_relevant`), 치유로 골리지도(`select_curated_authority_sources`)
#     않는다 — `title_names_medical_subject`와 같은 술어(`keyword_names_provider`).
# (c) '병원 고를 때'도 병원 선택 글이다('고르'와 '고를'은 다른 음절).

LOW_BACK_URL = CURATED_LOW_BACK
DISC_URL = KDCA_VIEW.format(3348)
KNEE_URL = KDCA_VIEW.format(5969)
HYPERTENSION_URL = KDCA_VIEW.format(6765)


def _prompt_of(monkeypatch, brief) -> str:
    """`_generate_content_attempt`가 공급자에 보내는 요청 전체(JSON 문자열)."""

    sent: list[dict] = []

    def capture(*_args, **kwargs):
        sent.append(kwargs)
        raise ValueError("stop after the prompt")  # 검증 오류 계열이라 재시도하지 않는다

    _stub_writer(monkeypatch, {})
    monkeypatch.setattr(content_engine.client.chat.completions, "create", capture)
    with pytest.raises(ValueError):
        asyncio.run(
            content_engine._generate_content_attempt(
                _mapo_hospital(), ContentType.TREATMENT, content_brief=brief
            )
        )
    assert len(sent) == 1
    return json.dumps(sent[0], ensure_ascii=False, default=str)


# 각 브리프는 진료비·병원 선택 칸 하나와, 그것만 없으면 요통 문서를 힌트로 받게 하는 의료 키워드를 함께 싣는다.
NO_SOURCE_BRIEFS = {
    "cost_target_query": {"target_query": "도수치료 비용", "target_keyword": "도수치료"},
    "cost_target_keyword": {"target_query": "노원 허리통증 도수치료", "target_keyword": "도수치료 비용"},
    "cost_query_target_name": {
        "target_query": "노원 도수치료",
        "target_keyword": "도수치료",
        "query_target": {"name": "노원 도수치료 가격 얼마예요", "treatment": "도수치료"},
    },
    "choice_target_query": {
        "target_query": "노원구 정형외과 병원 추천해줘",
        "target_keyword": "허리통증",
    },
    "choice_goreul": {
        "target_query": "노원 정형외과 병원 고를 때 확인할 점",
        "target_keyword": "허리디스크",
    },
    # 질문 칸만 진료비다(질의·키워드·질문 이름은 의료 주제) — 그 칸도 따로 본다(리뷰 3차 q04).
    "cost_target_question_only": {
        "target_query": "노원 도수치료 효과",
        "target_keyword": "도수치료",
        "target_question": "도수치료 비용은 얼마인가요?",
        "query_target": {"name": "노원 도수치료", "treatment": "도수치료"},
    },
}


@pytest.mark.parametrize("name", sorted(NO_SOURCE_BRIEFS))
def test_prompt_offers_no_curated_document_for_a_cost_or_choice_brief(monkeypatch, name):
    brief = NO_SOURCE_BRIEFS[name]

    assert content_engine._topic_aligned_curated_sources(dict(brief)) == []
    assert LOW_BACK_URL not in _prompt_of(monkeypatch, dict(brief))


@pytest.mark.parametrize("name", sorted(NO_SOURCE_BRIEFS))
def test_the_cost_or_choice_brief_would_get_the_hint_without_the_rule(monkeypatch, name):
    """대조군 — 브리프 판정을 끄면 같은 브리프가 요통 문서를 힌트로 받는다(위 테스트의 전제)."""

    monkeypatch.setattr(content_engine, "_brief_names_no_source_topic", lambda _brief: False)

    assert LOW_BACK_URL in _prompt_of(monkeypatch, dict(NO_SOURCE_BRIEFS[name]))


def test_a_medical_brief_still_gets_its_curated_hint(monkeypatch):
    brief = {"target_query": "노원 도수치료 효과와 횟수", "target_keyword": "도수치료"}

    assert LOW_BACK_URL in {ref["url"] for ref in content_engine._topic_aligned_curated_sources(brief)}
    assert LOW_BACK_URL in _prompt_of(monkeypatch, brief)


def test_the_post_generation_heal_still_judges_by_the_writer_title():
    """오탐이면 힌트만 빠진다 — 생성 뒤 치유는 브리프가 아니라 작가 제목으로 판정한다(98f586a8)."""

    brief = NO_SOURCE_BRIEFS["cost_target_query"]
    medical = content_engine._topic_aligned_curated_sources(
        dict(brief), {"title": "도수치료, 허리통증에 어떻게 쓰이나요", "reference_checks": []}
    )
    assert LOW_BACK_URL in {ref["url"] for ref in medical}
    assert (
        content_engine._topic_aligned_curated_sources(
            {"target_query": "노원 도수치료 효과"}, {"title": "도수치료 비용 안내"}
        )
        == []
    )


# 진료과 이름·병원 고르기 경로 키워드만 담은 제목. 제목에 의료 주제가 없다.
PROVIDER_ONLY_TITLES = [
    "노원구 마취통증의학과 병원 추천해 주시겠어요?",
    "노원구 마취통증의학과 전문의 추천은 어디인가요?",
    "정형외과 병원 추천",
    "정형외과 병원 고를 때 확인할 점",
    "경산 정형외과 병원 찾을 때 볼 것",
    "정형외과 잘하는 곳 찾는 법",
    "노원구 정형외과 병원 선택 기준 — 통증 종류별 진단·치료 항목 비교",
    "심장내과 순환기내과 진료 안내",
]


@pytest.mark.parametrize("title", PROVIDER_ONLY_TITLES)
def test_a_provider_or_routing_keyword_alone_never_passes_any_curated_document(title):
    """카탈로그 키워드와 카탈로그 제목(대조의 두 갈래) 어느 쪽으로도 통과하지 않는다."""

    terms = article_topic_terms(title=title, body="진료 기준을 안내합니다.", content_brief=None)
    passing = [url for url in CURATED_SOURCE_URLS if curated_topic_relevant(url, terms)]

    assert passing == []
    assert curated_sources_for_topic(terms) == []
    assert select_curated_authority_sources(title) == []


@pytest.mark.parametrize("url", [LOW_BACK_URL, DISC_URL])
@pytest.mark.parametrize("title", PROVIDER_ONLY_TITLES[:3])
async def test_a_provider_choice_post_citing_the_spine_documents_is_not_curated_verified(url, title):
    fetcher = PageFetcher()
    fetcher.add_document(url, "요통 | 국가건강정보포털 | 질병관리청", topic="요통")
    terms = article_topic_terms(title=title, body="진료 기준을 안내합니다.", content_brief=None)

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "요통", "url": url}], topic_terms=terms
    )

    assert outcome.kept == []
    assert [check["reason"] for check in outcome.checks] == ["unrelated_topic"]


@pytest.mark.parametrize(
    "title, url",
    [
        ("노원 허리디스크 도수치료 안내", LOW_BACK_URL),
        ("노원 허리디스크 도수치료 안내", DISC_URL),
        ("정형외과에서 보는 요통의 원인", LOW_BACK_URL),
        ("심장내과에서 고혈압을 관리하는 법", HYPERTENSION_URL),
        ("무릎관절염 운동, 정형외과에서 알려드립니다", KNEE_URL),
    ],
)
async def test_a_medical_title_still_passes_its_curated_document(title, url):
    fetcher = PageFetcher()
    fetcher.add_document(url, "문서 | 국가건강정보포털 | 질병관리청", topic="문서")
    terms = article_topic_terms(title=title, body="진료 기준을 안내합니다.", content_brief=None)

    outcome = await ReferenceVerifier(fetcher, domain_spacing=0).verify(
        [{"title": "문서", "url": url}], topic_terms=terms
    )

    assert [ref["url"] for ref in outcome.kept] == [url]
    assert [check["reason"] for check in outcome.checks] == ["curated_verified"]


def test_provider_keywords_are_exactly_the_specialty_and_routing_keywords():
    """치유 선택·수기 문서 대조·의료 주제 판정이 버리는 키워드 — 질환·시술 키워드는 남는다."""

    dropped = sorted(
        {
            keyword
            for source in CURATED_MEDICAL_SOURCE_PAGES
            for keyword in source["keywords"]
            if keyword_names_provider(keyword)
        }
    )
    assert dropped == sorted({"정형외과", "병원선택", "병원선택기준", "통증종류", "통증종류별", "심장내과", "순환기내과"})
    for keyword in ("도수치료", "허리디스크", "요통", "척추", "디스크", "고혈압", "치질", "대장내시경"):
        assert not keyword_names_provider(keyword)


@pytest.mark.parametrize(
    "title",
    [
        "경산 정형외과 병원 찾을 때 볼 것",
        "정형외과 잘하는 곳 찾는 법",
        "노원구 정형외과 통증 종류별 진료 흐름",
    ],
)
async def test_a_routing_only_post_gets_no_heal_at_publication(title):
    """병원 선택으로 분류되지 않는 제목도 경로 키워드만으로는 요통·무릎·디스크 문서를 받지 않는다."""

    assert topic_without_authoritative_source(title) is None
    item = _published(title, tasks.ContentStatus.DRAFT)
    fetcher = _hemorrhoid_fetcher()

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.references == [] and not refresh.healed
    assert not refresh.operator_decides  # 의료 글 규칙 — 종전 MISSING_REFERENCES 사다리
    assert fetcher.calls == [GUESSED]


@pytest.mark.parametrize(
    "title",
    [
        "정형외과 병원 고를 때 확인할 점",
        "경산 내과 병원을 고를 때 체크리스트",
        "피부과 의원 고를 때 알아둘 것",
        "소아청소년과 전문의를 고를 때",
    ],
)
async def test_choosing_a_clinic_with_goreul_is_a_provider_choice_post(title):
    assert topic_without_authoritative_source(title) == NO_SOURCE_TOPIC_PROVIDER_CHOICE
    item = _published(title, tasks.ContentStatus.DRAFT)
    fetcher = _hemorrhoid_fetcher()

    refresh = await refresh_publication_references(item, ReferenceVerifier(fetcher, domain_spacing=0))

    assert refresh.operator_decides and not refresh.healed and refresh.references == []
    assert fetcher.calls == [GUESSED]


def test_goreul_with_a_medical_subject_stays_a_medical_post():
    assert topic_without_authoritative_source("허리디스크 병원 고를 때 확인할 점") is None


# ── (viii) 저장된 사람 결정 표시는 아직 쓰이지 않은 슬롯에서만 판정이 된다 ─────────────────


def _row_with_operator_flag(title, body):
    row = _slot(title)
    row.body = body
    row.essence_check_summary = {
        "generation_attempt": {"reason": "MISSING_REFERENCES", OPERATOR_DECIDES_KEY: True}
    }
    return row


def test_a_leftover_operator_flag_does_not_decide_a_written_medical_post():
    """사람이 본문을 쓴 뒤의 의료 글은 제목이 판정한다 — 남은 표시로 수리 대상에서 빠지지 않는다."""

    written = _row_with_operator_flag("치질 수술 후 회복 기간", "## 회복\n사람이 쓴 본문입니다.")
    unwritten = _row_with_operator_flag(None, None)

    assert not operator_decides_references("MISSING_REFERENCES", written)
    assert operator_decides_references("MISSING_REFERENCES", unwritten)


# ── (ix) 관리자 PATCH: 발행 전 진료비·병원 선택 글에 수기 목록 문서를 넣지 못한다(422) ─────────


def _patch_setup(monkeypatch, *, title, status="DRAFT", references=None, **overrides):
    hospital = _hospital(site_live=False)
    item = _content_item(
        hospital_id=hospital.id,
        title=title,
        status=status,
        content_type=overrides.pop("content_type", "TREATMENT"),
        references_list=list(references or []),
        **overrides,
    )
    item.content_revision = 3
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    return hospital, item


def _patch(hospital, item, **fields):
    return content_api.update_content(
        hospital.id, item.id, content_api.ContentPatch(**fields), db=_PatchDB(hospital)
    )


def _spine_and_hemorrhoid_fetcher():
    fetcher = _hemorrhoid_fetcher()
    fetcher.add_document(LOW_BACK_URL, "요통 | 국가건강정보포털 | 질병관리청", topic="요통")
    fetcher.add_document(UNLISTED_HEMORRHOID, "치질 | 국가건강정보포털 | 질병관리청", topic="치질")
    return fetcher


@pytest.mark.parametrize("status", ["DRAFT", "READY"])
@pytest.mark.parametrize(
    "title, curated",
    [(COST_TITLE, CURATED_HEMORRHOID_KDCA), (CHOICE_TITLE, LOW_BACK_URL)],
    ids=["cost", "provider_choice"],
)
async def test_patch_rejects_a_curated_document_on_a_scheduled_cost_or_choice_post(
    monkeypatch, title, curated, status
):
    hospital, item = _patch_setup(monkeypatch, title=title, status=status)
    fetcher = _spine_and_hemorrhoid_fetcher()

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await _patch(
            hospital,
            item,
            references=[
                {"title": "치질", "url": UNLISTED_HEMORRHOID},
                {"title": "문서", "url": curated},
            ],
        )

    assert raised.value.status_code == 422
    detail = raised.value.detail
    assert detail["code"] == "CURATED_REFERENCE_NOT_ALLOWED"
    assert detail["urls"] == [curated]
    assert "진료비·병원 선택 글" in detail["message"] and curated in detail["message"]
    assert fetcher.calls == []  # 네트워크 전에 거절한다
    assert item.references_list == [] and item.content_revision == 3


async def test_patch_judges_the_title_it_saves(monkeypatch):
    """제목도 함께 바꾸면 저장될 제목으로 판정한다 — 의료 → 진료비는 거절, 진료비 → 의료는 받는다."""

    hospital, item = _patch_setup(monkeypatch, title=HEMORRHOID_TITLE)
    with override_reference_fetcher(PageFetcher()), pytest.raises(HTTPException) as raised:
        await _patch(
            hospital,
            item,
            title=COST_TITLE,
            references=[{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}],
        )
    assert raised.value.status_code == 422
    assert item.title == HEMORRHOID_TITLE

    hospital, item = _patch_setup(monkeypatch, title=COST_TITLE)
    with override_reference_fetcher(_hemorrhoid_fetcher()):
        await _patch(
            hospital,
            item,
            title=HEMORRHOID_TITLE,
            references=[{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}],
        )
    assert [ref["url"] for ref in item.references_list] == [CURATED_HEMORRHOID_KDCA]


async def test_patch_keeps_an_ordinary_passing_url_on_a_cost_post(monkeypatch):
    hospital, item = _patch_setup(monkeypatch, title=COST_TITLE)
    fetcher = _spine_and_hemorrhoid_fetcher()

    with override_reference_fetcher(fetcher):
        await _patch(hospital, item, references=[{"title": "치질", "url": UNLISTED_HEMORRHOID}])

    assert [ref["url"] for ref in item.references_list] == [UNLISTED_HEMORRHOID]
    assert fetcher.calls == [UNLISTED_HEMORRHOID]
    assert [check["reason"] for check in item.reference_checks] == ["page_verified"]


async def test_patch_accepts_a_curated_document_on_a_medical_post(monkeypatch):
    hospital, item = _patch_setup(monkeypatch, title=HEMORRHOID_TITLE)

    with override_reference_fetcher(_hemorrhoid_fetcher()):
        await _patch(hospital, item, references=[{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}])

    assert [ref["url"] for ref in item.references_list] == [CURATED_HEMORRHOID_KDCA]
    assert [check["reason"] for check in item.reference_checks] == ["curated_verified"]


@pytest.mark.parametrize("status", ["PUBLISHED", "WITHHELD"])
async def test_patch_on_a_published_cost_post_is_unaffected(monkeypatch, status):
    """공개·보존된 글은 이 규칙 밖이다 — 사람의 편집은 종전처럼 검증을 통과하면 저장된다."""

    references = [{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}]
    hospital, item = _patch_setup(
        monkeypatch, title=COST_TITLE, status=status, references=[dict(ref) for ref in references]
    )

    with override_reference_fetcher(_hemorrhoid_fetcher()):
        await _patch(hospital, item, references=[dict(ref) for ref in references])

    assert [ref["url"] for ref in item.references_list] == [CURATED_HEMORRHOID_KDCA]
    assert [check["reason"] for check in item.reference_checks] == ["curated_verified"]


async def test_patch_refuses_when_the_title_turns_cost_during_the_check(monkeypatch):
    """GET 사이에 다른 편집이 제목을 진료비 글로 바꾸면 스냅샷 대조가 409로 막는다."""

    hospital, item = _patch_setup(monkeypatch, title=HEMORRHOID_TITLE)

    def concurrent_title_edit():
        item.title = COST_TITLE

    fetcher = _MutatingFetcher(concurrent_title_edit)
    fetcher.add_document(CURATED_HEMORRHOID_KDCA, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await _patch(hospital, item, references=[{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}])

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "CONTENT_CHANGED_DURING_REFERENCE_CHECK"
    assert item.references_list == []


async def test_patch_rechecks_on_the_locked_row_when_the_post_becomes_reference_required(
    monkeypatch,
):
    """필수 여부(질문 연결)는 주제 지문 밖이다 — GET 사이에 공지가 질문에 연결되면 잠근 행으로 거절한다."""

    notice_title = "치질 수술 비용 안내"
    hospital, item = _patch_setup(
        monkeypatch, title=notice_title, content_type="NOTICE", query_target_id=None
    )
    assert not references_left_to_operator(item)  # 순수 운영 공지 — 참고자료 필수가 아니다

    def link_a_query_target():
        item.query_target_id = uuid.uuid4()

    fetcher = _MutatingFetcher(link_a_query_target)
    fetcher.add_document(CURATED_HEMORRHOID_KDCA, "치핵 | 국가건강정보포털 | 질병관리청", topic="치핵")

    with override_reference_fetcher(fetcher), pytest.raises(HTTPException) as raised:
        await _patch(hospital, item, references=[{"title": "치핵", "url": CURATED_HEMORRHOID_KDCA}])

    assert fetcher.calls == [CURATED_HEMORRHOID_KDCA]  # 잠그기 전 검사는 통과했다
    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "CURATED_REFERENCE_NOT_ALLOWED"
    assert item.references_list == []


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
