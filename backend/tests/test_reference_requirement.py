"""참고자료 필수 판정 한 규칙 — 의료 주제 NOTICE(2af00d02)와 순수 운영 공지(5821409e) 양쪽을 고정한다.

2af00d02(마포성모탑정형외과, '마포구 신경외과 진료 — 증상·검사·치료 흐름 안내'): content_type
NOTICE + 측정 질문 f1de79a2('마포구 신경외과 병원 추천해줘', 진료과 신경외과). 모델이 쓴 참고자료
2개가 도메인 루트(https://health.kdca.go.kr, https://www.neurosurgery.or.kr)라 둘 다 빠져 0개가
됐는데, NOTICE라 생성(규칙·스키마·GEO hard-fail·수기 목록 치유)과 발행 게이트가 모두 면제해
참고자료 0개로 자동 공개됐다.

5821409e: 순수 운영 공지까지 발행 게이트가 참고자료를 요구하면 생성은 되고 발행은 매일
MISSING_REFERENCES로 막히다 영구 DRAFT가 된다. 이 면제는 그대로다.

네트워크는 쓰지 않는다 — 가짜 fetcher만 쓴다. 운영 글(2af00d02 포함)은 건드리지 않는다: 아래 행은
모두 테스트 안에서 만든 모양이다.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import arrow
import pytest
from fastapi import HTTPException

from app.api.admin import content as content_api
from app.models.content import ContentType
from app.services import content_engine, cost_guard, provider_usage
from app.services.audit_log import reset_request_actor, set_request_actor
from app.services.content_publication import (
    has_required_references,
    public_surface_has_required_references,
)
from app.services.reference_publication import (
    publication_references_missing,
    publication_references_settled,
)
from app.services.reference_requirement import (
    REFERENCES_REQUIRED_TYPES,
    brief_carries_medical_topic,
    references_required,
    references_required_for,
)
from app.services.reference_verification import ReferenceVerifier, override_reference_fetcher
from app.workers import tasks
from app.workers.generation_run_control import (
    GENERATION_REFERENCE_REJECTION_MESSAGE,
    classify_generation_failure,
)
from tests.reference_fetch_doubles import PageFetcher
from tests.test_content_compliance_faq import _content_item, _hospital, _NoExecuteDB, _wire
from tests.test_reference_publication_gate import (
    KDCA_VIEW,
    _CommitDB,
    _gate_setup,
    _GateDB,
    _publish_setup,
)
from tests.test_tasks_nightly import _publication_hospital, _publication_item

ROOT_URLS = [
    {"title": "질병관리청", "url": "https://health.kdca.go.kr"},
    {"title": "대한신경외과학회", "url": "https://www.neurosurgery.or.kr"},
]
CURATED_HYPERTENSION = KDCA_VIEW.format(6765)
_TARGET_ID = "f1de79a2-0000-4000-8000-000000000000"


def _brief_2af00d02() -> dict:
    """2af00d02의 브리프 모양 — 노출 계획이 고른 질문(진료과 신경외과)에 연결돼 있다."""
    return {
        "target_query": "마포구 신경외과 병원 추천해줘",
        "target_keyword": "신경외과",
        "target_question": "마포구 신경외과 병원은 어떻게 고르나요?",
        "target_region_terms": ["마포구"],
        "query_target": {
            "id": _TARGET_ID,
            "name": "마포구 신경외과 병원 추천해줘",
            "treatment": None,
            "condition_or_symptom": None,
            "specialty": "신경외과",
        },
        "exposure_action": None,
        "treatment_narrative": {"source": "fallback"},
        "source": {"mode": "automatic_exposure_plan"},
    }


def _brief_hypertension() -> dict:
    return {
        "target_query": "마포 고혈압 병원",
        "target_keyword": "고혈압",
        "query_target": {"id": str(uuid.uuid4()), "name": "마포 고혈압 병원", "condition_or_symptom": "고혈압"},
        "treatment_narrative": {"source": "fallback"},
        "source": {"mode": "automatic_exposure_plan"},
    }


def _brief_operational_notice() -> dict:
    """노출 계획이 질문을 고르지 못한 공지 슬롯의 실제 모양.

    계획기는 이런 슬롯에도 `automatic_exposure_plan`을 찍고, `target_keyword`는 슬롯 제목에서,
    `treatment_narrative`는 병원의 첫 진료 항목에서 채운다 — 그래도 의료 주제가 아니다.
    """
    return {
        "target_query": "NOTICE content slot",
        "target_keyword": "NOTICE",
        "query_target": None,
        "exposure_action": None,
        "treatment_narrative": {"source": "hospital_profile", "treatment": "도수치료"},
        "source": {"mode": "automatic_exposure_plan"},
    }


# ── 판정 한 규칙 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("content_type", sorted(REFERENCES_REQUIRED_TYPES, key=str))
def test_medical_types_always_require_references(content_type):
    assert references_required_for(content_type)
    assert references_required_for(content_type.value, content_brief=_brief_operational_notice())


def test_medical_notice_2af00d02_shape_requires_references():
    assert references_required_for(
        ContentType.NOTICE, content_brief=_brief_2af00d02(), query_target_id=_TARGET_ID
    )
    # 행의 질문 연결만 있어도(브리프가 오래됐어도) 필수다.
    assert references_required_for("NOTICE", query_target_id=_TARGET_ID)
    # 브리프의 질문 참조만 있어도 필수다.
    assert references_required_for("NOTICE", content_brief=_brief_2af00d02())
    # 노출 과제가 질문을 가리키는 브리프도 같다.
    assert references_required_for(
        "NOTICE", content_brief={"exposure_action": {"query_target_id": _TARGET_ID}}
    )


@pytest.mark.parametrize("field", ["name", "treatment", "condition_or_symptom", "specialty"])
def test_any_medical_field_of_the_linked_question_marks_the_notice(field):
    assert brief_carries_medical_topic({"query_target": {field: "신경외과"}})


@pytest.mark.parametrize(
    "brief",
    [
        None,
        {},
        _brief_operational_notice(),
        {"query_target": {"id": "", "name": " ", "specialty": None}},
        {"exposure_action": {"query_target_id": None}},
    ],
)
def test_pure_operational_notice_stays_exempt(brief):
    """5821409e — 질문이 연결되지 않은 공지는 생성·발행 모두 참고자료를 요구하지 않는다."""
    assert not references_required_for(ContentType.NOTICE, content_brief=brief)
    assert not references_required_for("NOTICE", content_brief=brief, query_target_id=None)


def test_unreadable_type_requires_references():
    assert references_required_for(None)
    assert references_required_for("")


def test_item_level_predicate_reads_the_row():
    medical = SimpleNamespace(
        content_type="NOTICE", content_brief=None, query_target_id=uuid.UUID(_TARGET_ID)
    )
    operational = SimpleNamespace(
        content_type=ContentType.NOTICE, content_brief=_brief_operational_notice(), query_target_id=None
    )
    assert references_required(medical)
    assert not references_required(operational)


# ── 발행 게이트: 참고자료 필수 글은 확인된 참고자료 0개로 공개하지 않는다 ─────────────


def _notice_publish_setup(monkeypatch, *, references, brief, query_target_id, title):
    item, db, effects = _publish_setup(monkeypatch, references=references, checks=None, title=title)
    item.content_type = SimpleNamespace(value="NOTICE")
    item.image_subject_hash = tasks.image_subject_hash(item.content_type, title)
    item.faq_question = None
    item.faq_answer_summary = None
    item.content_brief = brief
    item.query_target_id = query_target_id
    return item, db, effects


@pytest.mark.parametrize("references", [[], ROOT_URLS], ids=["empty", "domain_roots"])
def test_zero_reference_publish_block_for_medical_notice_2af00d02(monkeypatch, references):
    """2af00d02 모양은 참고자료가 비었거나 도메인 루트뿐이면 0개로 공개되지 않는다.

    먼저 이 글의 주제로 수기 목록 치유를 시도하고(신경외과 질문에 맞는 수기 문서는 없다),
    그래도 없으면 MISSING_REFERENCES로 보류한다 — 원인과 맞는 문구로.
    """
    item, _db, effects = _notice_publish_setup(
        monkeypatch,
        references=[dict(ref) for ref in references],
        brief=_brief_2af00d02(),
        query_target_id=uuid.UUID(_TARGET_ID),
        title="마포구 신경외과 진료 — 증상·검사·치료 흐름 안내",
    )
    fetcher = PageFetcher()
    # 비었거나 확인 기록이 없다 — 발행 경로가 재검증·치유를 먼저 돈다.
    assert not publication_references_settled(item)

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert payload is not None and payload["kind"] == "blocked"
    assert payload["code"] == "MISSING_REFERENCES"
    assert "공신력 있는 참고 자료를 확보하지 못했습니다" in payload["message"]
    assert item.status is tasks.ContentStatus.DRAFT
    assert item.published_at is None
    assert item.references_list == []
    assert effects == {"revalidate": [], "indexnow": []}
    assert fetcher.calls == []  # 도메인 루트는 문서가 아니다 — GET할 것도 없다
    if references:
        assert {check["reason"] for check in item.reference_checks} == {"not_citable"}


@pytest.mark.parametrize("with_brief", [True, False], ids=["brief", "row_link_only"])
def test_medical_notice_with_no_references_is_healed_from_the_curated_list_first(
    monkeypatch, with_brief
):
    """비어 있는 필수 글은 보류 전에 이 글의 주제로 수기 목록 치유를 먼저 한다.

    `row_link_only`: 브리프 없이 행의 질문 연결(`query_target_id`)만 있어도 같다 — 잠금 전
    재검증이 읽는 행 보기(`_REFERENCE_VIEW_FIELDS`)도 그 연결을 싣는다.
    """
    item, _db, _effects = _notice_publish_setup(
        monkeypatch,
        references=[],
        brief=_brief_hypertension() if with_brief else None,
        query_target_id=uuid.uuid4(),
        title="마포 고혈압 진료 안내",
    )
    fetcher = PageFetcher()
    fetcher.add_document(CURATED_HYPERTENSION, "고혈압 | 국가건강정보포털 | 질병관리청", topic="고혈압")

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert payload is None or payload.get("kind") != "blocked"
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert [ref["url"] for ref in item.references_list] == [CURATED_HYPERTENSION]
    assert fetcher.calls == [CURATED_HYPERTENSION]


def test_pure_operational_notice_still_publishes_without_references(monkeypatch):
    """5821409e 회귀 방지 — 질문 연결 없는 공지는 참고자료 없이도 발행된다."""
    item, _db, _effects = _notice_publish_setup(
        monkeypatch,
        references=[],
        brief=_brief_operational_notice(),
        query_target_id=None,
        title="추석 연휴 진료 안내",
    )
    fetcher = PageFetcher()

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert payload is None or payload.get("kind") != "blocked", payload
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert fetcher.calls == []


def test_seven_forty_five_holds_a_medical_notice_without_references(monkeypatch):
    hospital = _publication_hospital()
    title = "마포구 신경외과 진료 — 증상·검사·치료 흐름 안내"
    item = _publication_item(hospital, body="진료 흐름을 안내합니다.", title=title)
    item.content_type = SimpleNamespace(value="NOTICE")
    item.image_subject_hash = tasks.image_subject_hash(item.content_type, title)
    item.faq_question = None
    item.faq_answer_summary = None
    item.content_brief = _brief_2af00d02()
    item.query_target_id = uuid.UUID(_TARGET_ID)
    item.references_list = [dict(ref) for ref in ROOT_URLS]
    item.reference_checks = None
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)

    with override_reference_fetcher(PageFetcher()):
        paged = tasks._page_morning_stored_publication_gates(
            db, now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )

    assert paged == 1
    assert incidents[0]["code"] == "MISSING_REFERENCES"
    assert "공신력 있는 참고 자료를 확보하지 못했습니다" in incidents[0]["message"]
    assert item.references_list == []


def test_seven_forty_five_heals_an_empty_medical_notice_before_paging(monkeypatch):
    hospital = _publication_hospital()
    title = "마포 고혈압 진료 안내"
    item = _publication_item(hospital, body="혈압 관리를 안내합니다.", title=title)
    item.content_type = SimpleNamespace(value="NOTICE")
    item.image_subject_hash = tasks.image_subject_hash(item.content_type, title)
    item.faq_question = None
    item.faq_answer_summary = None
    item.content_brief = _brief_hypertension()
    item.query_target_id = uuid.uuid4()
    item.references_list = []
    item.reference_checks = None
    db = _GateDB(item)
    incidents = _gate_setup(monkeypatch, item)
    fetcher = PageFetcher()
    fetcher.add_document(CURATED_HYPERTENSION, "고혈압 | 국가건강정보포털 | 질병관리청", topic="고혈압")

    with override_reference_fetcher(fetcher):
        paged = tasks._page_morning_stored_publication_gates(
            db, now_kst=arrow.get(2026, 6, 10, 7, 45, tzinfo="Asia/Seoul")
        )

    assert paged == 0
    assert incidents == []
    assert [ref["url"] for ref in item.references_list] == [CURATED_HYPERTENSION]


async def test_manual_publish_heals_an_empty_medical_notice_before_the_reference_gate(monkeypatch):
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        content_type="NOTICE",
        title="마포 고혈압 진료 안내",
        references_list=[],
        content_brief=_brief_hypertension(),
        query_target_id=uuid.uuid4(),
    )
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)

    async def no_philosophy(*_args):
        return None

    monkeypatch.setattr(content_api, "_get_approved_philosophy", no_philosophy)
    fetcher = PageFetcher()
    fetcher.add_document(CURATED_HYPERTENSION, "고혈압 | 국가건강정보포털 | 질병관리청", topic="고혈압")
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(fetcher):
            with pytest.raises(HTTPException) as raised:
                await content_api.publish_content(
                    hospital.id, item.id, content_api.PublishBody(), db=_NoExecuteDB()
                )
    finally:
        reset_request_actor(token)

    # 참고자료는 치유됐다 — 이 더블 DB에는 승인된 운영 기준이 없어 다음 게이트에서 멈춘다.
    assert [ref["url"] for ref in item.references_list] == [CURATED_HYPERTENSION]
    assert raised.value.detail.get("missing") != "references", raised.value.detail


async def test_manual_publish_refuses_a_medical_notice_without_references(monkeypatch):
    hospital = _hospital()
    item = _content_item(
        hospital_id=hospital.id,
        content_type="NOTICE",
        title="마포구 신경외과 진료 — 증상·검사·치료 흐름 안내",
        references_list=[dict(ref) for ref in ROOT_URLS],
        content_brief=_brief_2af00d02(),
        query_target_id=uuid.UUID(_TARGET_ID),
    )
    item.reference_checks = None
    _wire(monkeypatch, item, hospital)
    token = set_request_actor("ae@example.com")
    try:
        with override_reference_fetcher(PageFetcher()):
            with pytest.raises(HTTPException) as raised:
                await content_api.publish_content(
                    hospital.id, item.id, content_api.PublishBody(), db=_NoExecuteDB()
                )
    finally:
        reset_request_actor(token)

    assert raised.value.status_code == 400, raised.value.detail
    assert raised.value.detail["missing"] == "references", raised.value.detail
    assert item.status == "DRAFT"


async def test_restore_refuses_a_medical_notice_without_references_and_never_fills_them():
    item = SimpleNamespace(
        title="마포구 신경외과 진료 — 증상·검사·치료 흐름 안내",
        body="",
        content_type="NOTICE",
        content_brief=_brief_2af00d02(),
        query_target_id=uuid.UUID(_TARGET_ID),
        faq_question=None,
        content_revision=4,
        references_list=[],
        reference_checks=None,
    )
    db = _CommitDB()

    with pytest.raises(HTTPException) as raised:
        await content_api._require_restorable_references(db, item, None)

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "MISSING_REFERENCES"
    assert "PATCH" in raised.value.detail["message"]
    assert item.references_list == []  # restore는 채우지 않는다
    assert item.content_revision == 4
    assert db.commits == 0


async def test_restore_keeps_the_pure_operational_notice_exemption():
    item = SimpleNamespace(
        title="추석 연휴 진료 안내",
        body="",
        content_type="NOTICE",
        content_brief=_brief_operational_notice(),
        query_target_id=None,
        faq_question=None,
        content_revision=4,
        references_list=[],
        reference_checks=None,
    )

    await content_api._require_restorable_references(_CommitDB(), item, None)

    assert not publication_references_missing(item)


def test_public_surface_keeps_the_type_rule_so_published_posts_do_not_change():
    """이미 공개된 의료 주제 NOTICE(2af00d02 모양)를 배포만으로 사이트에서 내리지 않는다.

    새 발행·restore는 새 규칙으로 막고, 공개 글 교정은 사람이 따로 한다.
    """
    published = SimpleNamespace(
        content_type="NOTICE",
        content_brief=_brief_2af00d02(),
        query_target_id=uuid.UUID(_TARGET_ID),
        references_list=[],
    )
    assert public_surface_has_required_references(published)
    assert not has_required_references(published)


# ── 생성: 의료 주제 NOTICE는 규칙·스키마·치유·거절이 의료 안내 유형과 같다 ────────────


def _mapo_hospital():
    return SimpleNamespace(
        name="마포성모탑정형외과",
        address="서울 마포구",
        phone="02-000-0000",
        business_hours="",
        region=["마포"],
        specialties=["정형외과"],
        keywords=["척추"],
        director_name="김원장",
        director_career="",
        director_philosophy="",
        treatments=[],
    )


def test_medical_notice_prompt_carries_the_reference_rule_and_the_pure_notice_does_not():
    hospital = _mapo_hospital()

    medical = content_engine._fill_type_prompt(ContentType.NOTICE, hospital, _brief_2af00d02())
    operational = content_engine._fill_type_prompt(
        ContentType.NOTICE, hospital, _brief_operational_notice()
    )

    assert content_engine.TYPE_PROMPT_REFERENCE_RULE in medical
    assert content_engine.TYPE_PROMPT_REFERENCE_RULE not in operational


def test_medical_notice_gets_the_reference_schema():
    assert (
        content_engine._article_tool_schema(ContentType.NOTICE, _brief_2af00d02())
        is content_engine._REFERENCE_REQUIRED_ARTICLE_INPUT_SCHEMA
    )
    assert (
        content_engine._article_tool_schema(ContentType.NOTICE, _brief_operational_notice())
        is content_engine.ARTICLE_TOOL["input_schema"]
    )


def _notice_payload(references):
    body = (
        "## 마포구 신경외과 진료 흐름\n마포성모탑정형외과 김원장은 마포에서 증상을 먼저 확인합니다. "
        + ("목과 허리 통증은 자세와 생활 습관에 따라 달라질 수 있어 진찰로 원인을 살핍니다. " * 30)
        + "\n\n## 검사와 치료\n"
        + ("영상 검사가 필요한지는 진찰 결과로 정하며 치료는 단계적으로 진행합니다. " * 30)
    )
    return {
        "title": "마포구 신경외과 진료 — 증상·검사·치료 흐름 안내",
        "body": body,
        "meta_description": "마포구 신경외과 진료의 증상 확인, 검사, 치료 흐름을 안내합니다.",
        "references": references,
        "faq_question": None,
        "faq_answer_summary": None,
    }


def _stub_writer(monkeypatch, payload):
    calls = {"n": 0}

    class _FakeResponse:
        content = [SimpleNamespace(text=json.dumps(payload))]

    def fake_create(*_args, **_kwargs):
        calls["n"] += 1
        return _FakeResponse()

    async def no_usage_record(*_args, **_kwargs):
        return None

    monkeypatch.setattr(content_engine.client.chat.completions, "create", fake_create)
    monkeypatch.setattr(content_engine, "GENERATION_REMEDIATION_ROUNDS", 1)
    # 비용·사용량 기록은 이 테스트의 관심사가 아니다(DB·Redis 없이 돈다).
    monkeypatch.setattr(cost_guard, "record_provider_call", no_usage_record)
    monkeypatch.setattr(provider_usage, "record_attempt", no_usage_record)
    return calls


async def test_medical_notice_whose_references_all_drop_is_rejected_with_the_reference_copy(
    monkeypatch,
):
    """2af00d02 생성 경로: 도메인 루트 2개가 빠져 0개면 조용히 저장하지 않고 거절한다."""
    _stub_writer(monkeypatch, _notice_payload([dict(ref) for ref in ROOT_URLS]))

    with override_reference_fetcher(PageFetcher()):
        with pytest.raises(content_engine.MissingCitableReferencesError) as raised:
            await content_engine.generate_content(
                _mapo_hospital(), ContentType.NOTICE, content_brief=_brief_2af00d02()
            )

    code, message = classify_generation_failure(raised.value)
    assert code == "GENERATION_REJECTED"
    assert message == GENERATION_REFERENCE_REJECTION_MESSAGE


async def test_medical_notice_whose_references_all_drop_is_healed_from_the_curated_list(
    monkeypatch,
):
    payload = _notice_payload([dict(ref) for ref in ROOT_URLS])
    payload["title"] = "마포 고혈압 진료 안내"
    calls = _stub_writer(monkeypatch, payload)
    fetcher = PageFetcher()
    fetcher.add_document(CURATED_HYPERTENSION, "고혈압 | 국가건강정보포털 | 질병관리청", topic="고혈압")

    with override_reference_fetcher(fetcher):
        result = await content_engine.generate_content(
            _mapo_hospital(), ContentType.NOTICE, content_brief=_brief_hypertension()
        )

    assert calls["n"] == 1
    assert [ref["url"] for ref in result["references"]] == [CURATED_HYPERTENSION]
    # 채운 문서도 같은 검증(실제로 열어 봄)을 거쳤다.
    assert fetcher.calls == [CURATED_HYPERTENSION]
    assert result["reference_checks"][-1]["reason"] == "curated_verified"


async def test_pure_operational_notice_generation_still_saves_without_references(monkeypatch):
    payload = _notice_payload([dict(ref) for ref in ROOT_URLS])
    payload["title"] = "추석 연휴 진료 안내"
    _stub_writer(monkeypatch, payload)

    with override_reference_fetcher(PageFetcher()):
        result = await content_engine.generate_content(
            _mapo_hospital(), ContentType.NOTICE, content_brief=_brief_operational_notice()
        )

    assert result["references"] == []
