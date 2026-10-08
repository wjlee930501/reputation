"""Real PostgreSQL proof for durable lifetime generation budgets."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from openai import OpenAI
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital, HospitalStatus
from app.services import (
    content_ai_review,
    content_engine,
    content_publication,
    cost_guard,
    provider_usage,
)
from app.services.cost_guard import CostGuardDecision
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED, EssenceScreeningResult
from app.workers import tasks
from app.workers.generation_attempt_state import (
    GenerationBudgetExceeded,
    GenerationBudgetLedger,
    fresh_generation_attempt,
    read_generation_budget,
)


def _factory(pg_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=pg_engine, expire_on_commit=False)


def _seed(factory: sessionmaker[Session]) -> tuple[uuid.UUID, uuid.UUID]:
    hospital_id = uuid.uuid4()
    item_id = uuid.uuid4()
    with factory() as db:
        hospital = Hospital(
            id=hospital_id,
            name="생성 예산 의원",
            slug=f"generation-budget-{uuid.uuid4().hex[:10]}",
            status=HospitalStatus.ACTIVE,
            region=["서울"],
            specialties=["내과"],
            keywords=["건강검진"],
            competitors=[],
            treatments=[],
        )
        schedule = ContentSchedule(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            plan="PLAN_12",
            publish_days=[0],
            active_from=date(2026, 10, 1),
        )
        item = ContentItem(
            id=item_id,
            hospital_id=hospital_id,
            schedule_id=schedule.id,
            content_type=ContentType.FAQ,
            sequence_no=1,
            total_count=12,
            scheduled_date=date(2026, 10, 10),
            status=ContentStatus.DRAFT,
            references_list=[],
            essence_check_summary={
                "generation_attempt": fresh_generation_attempt(topic_id="topic-a")
            },
        )
        db.add_all([hospital, schedule, item])
        db.commit()
    return hospital_id, item_id


def _cleanup(factory: sessionmaker[Session], hospital_id: uuid.UUID) -> None:
    with factory() as db:
        db.execute(delete(Hospital).where(Hospital.id == hospital_id))
        db.commit()


class _ProviderHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    image_status = 503

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append({"path": self.path, "payload": payload})
        if self.path == "/image":
            self.send_response(type(self).image_status)
            self.end_headers()
            return
        tool_name = payload["tools"][0]["function"]["name"]
        if tool_name == "emit_article":
            arguments = {
                "title": "건강검진 전 준비",
                "body": "## 준비 안내\n서울 생성 예산 의원 김예산 원장이 건강검진 준비를 안내합니다.",
                "meta_description": "서울 생성 예산 의원 건강검진 준비 안내",
                "references": [],
                "faq_question": None,
                "faq_answer_summary": None,
            }
        else:
            arguments = {
                "decision": "PASS",
                "confidence": 0.99,
                "findings": [],
                "summary": "검수 통과",
            }
        response = {
            "id": f"fake-{len(type(self).requests)}",
            "object": "chat.completion",
            "created": 1,
            "model": "fake-wire",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(arguments, ensure_ascii=False),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        body = json.dumps(response, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def provider_server():
    _ProviderHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_concurrent_restart_reads_one_durable_reservation(pg_engine) -> None:
    factory = _factory(pg_engine)
    hospital_id, item_id = _seed(factory)
    try:
        first_db = factory()
        second_db = factory()
        try:
            first_item = first_db.get(ContentItem, item_id)
            second_item = second_db.get(ContentItem, item_id)
            assert first_item is not None and second_item is not None

            first = GenerationBudgetLedger(first_db, first_item)
            second = GenerationBudgetLedger(second_db, second_item)
            reservation = first.reserve(acquisition_id="writer:topic-a:first", kind="WRITER")
            replay = second.reserve(acquisition_id="writer:topic-a:first", kind="WRITER")
            assert replay.cached_payload is None

            first.transport_started(reservation)
            first.complete(
                reservation,
                transport_attempts=1,
                outcome="RESULT",
                payload={"title": "durable candidate"},
            )
        finally:
            first_db.close()
            second_db.close()

        with factory() as restarted:
            item = restarted.get(ContentItem, item_id)
            assert item is not None
            budget = read_generation_budget(item.essence_check_summary["generation_attempt"])
            assert budget.writer_results == 1
            assert budget.text_http_attempts == 1
            assert budget.text_http_reserved == 0
    finally:
        _cleanup(factory, hospital_id)


def test_fake_transport_through_worker_review_flow_counts_actual_http(
    pg_engine, monkeypatch, provider_server
) -> None:
    factory = _factory(pg_engine)
    hospital_id, item_id = _seed(factory)
    try:
        wire_client = OpenAI(
            api_key="loopback-only",
            base_url=f"{provider_server}/v1",
            max_retries=0,
        )
        monkeypatch.setattr(content_engine, "client", wire_client)
        monkeypatch.setattr(content_ai_review, "_llm_client", lambda: wire_client)
        monkeypatch.setattr(content_engine.settings, "OPENROUTER_API_KEY", "loopback-only")

        async def allowed(*_args, **_kwargs):
            return CostGuardDecision(allowed=True)

        async def noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(cost_guard, "reserve", allowed)
        monkeypatch.setattr(cost_guard, "record_provider_call", noop)
        monkeypatch.setattr(cost_guard, "settle_reservation", noop)
        monkeypatch.setattr(provider_usage, "record_attempt", noop)
        monkeypatch.setattr(
            tasks,
            "screen_content_against_philosophy",
            lambda _item, _philosophy: EssenceScreeningResult(
                status=ESSENCE_STATUS_ALIGNED, summary={}
            ),
        )

        with factory() as db:
            item = db.get(ContentItem, item_id)
            hospital = db.get(Hospital, hospital_id)
            assert item is not None and hospital is not None
            item.content_type = ContentType.COLUMN
            hospital.director_name = "김예산"
            db.commit()
            philosophy = SimpleNamespace(
                id=uuid.uuid4(),
                status="APPROVED",
                version=1,
                positioning_statement="환자의 질문을 차분히 설명합니다.",
                doctor_voice="차분한 설명",
                patient_promise="확인 가능한 내용만 안내합니다.",
                content_principles=[],
                tone_guidelines=[],
                must_use_messages=[],
                avoid_messages=[],
                medical_ad_risk_rules=[],
                treatment_narratives=[],
                prefer_topics=[],
                prefer_messages=[],
            )
            content, _screening = tasks._run_async(
                tasks._generate_with_auto_review(
                    db=db,
                    hospital=hospital,
                    item=item,
                    existing_titles=[],
                    philosophy=philosophy,
                    approved_brief=None,
                )
            )
            assert content["title"] == "건강검진 전 준비"
            replayed, _replayed_screening = tasks._run_async(
                tasks._generate_with_auto_review(
                    db=db,
                    hospital=hospital,
                    item=item,
                    existing_titles=[],
                    philosophy=philosophy,
                    approved_brief=None,
                )
            )
            assert replayed == content
            assert [request["payload"]["tools"][0]["function"]["name"] for request in _ProviderHandler.requests] == [
                "emit_article",
                "report_review",
            ]
            philosophy.doctor_voice = "같은 승인본 ID 안에서 바뀐 설명 방식"
            changed, _changed_screening = tasks._run_async(
                tasks._generate_with_auto_review(
                    db=db,
                    hospital=hospital,
                    item=item,
                    existing_titles=[],
                    philosophy=philosophy,
                    approved_brief=None,
                )
            )
            assert changed["title"] == content["title"]
            assert changed["body"] == content["body"]
            assert [
                request["payload"]["tools"][0]["function"]["name"]
                for request in _ProviderHandler.requests
            ] == ["emit_article", "report_review", "emit_article", "report_review"]
            unchanged, _unchanged_screening = tasks._run_async(
                tasks._generate_with_auto_review(
                    db=db,
                    hospital=hospital,
                    item=item,
                    existing_titles=[],
                    philosophy=philosophy,
                    approved_brief=None,
                )
            )
            assert unchanged["body"] == changed["body"]
            assert len(_ProviderHandler.requests) == 4

            approved_brief = {
                "target_query": "바뀐 건강검진 준비 질문",
                "source_snapshot": {"hash": "brief-v2", "source_asset_ids": ["source-v2"]},
            }
            tasks._run_async(
                tasks._generate_with_auto_review(
                    db=db,
                    hospital=hospital,
                    item=item,
                    existing_titles=[],
                    philosophy=philosophy,
                    approved_brief=approved_brief,
                )
            )
            assert len(_ProviderHandler.requests) == 6
            tasks._run_async(
                tasks._generate_with_auto_review(
                    db=db,
                    hospital=hospital,
                    item=item,
                    existing_titles=[],
                    philosophy=philosophy,
                    approved_brief=approved_brief,
                )
            )
            assert len(_ProviderHandler.requests) == 6

        with factory() as verify:
            item = verify.get(ContentItem, item_id)
            assert item is not None
            budget = read_generation_budget(item.essence_check_summary["generation_attempt"])
            assert (budget.writer_results, budget.reviewer_results) == (3, 3)
            assert budget.text_http_attempts == 6
            assert budget.text_http_reserved == 0
    finally:
        _cleanup(factory, hospital_id)


def test_worker_writeback_keeps_lifetime_budget_across_restart_day_and_regeneration(
    pg_engine, monkeypatch, provider_server
) -> None:
    factory = _factory(pg_engine)
    hospital_id, item_id = _seed(factory)
    first_day = datetime(2026, 10, 9, 23, 0, tzinfo=UTC)

    class FrozenDateTime(datetime):
        current = first_day

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz is not None else cls.current.replace(tzinfo=None)

    try:
        wire_client = OpenAI(
            api_key="loopback-only",
            base_url=f"{provider_server}/v1",
            max_retries=0,
        )
        monkeypatch.setattr(content_engine, "client", wire_client)
        monkeypatch.setattr(content_ai_review, "_llm_client", lambda: wire_client)
        monkeypatch.setattr(content_engine.settings, "OPENROUTER_API_KEY", "loopback-only")
        monkeypatch.setattr(tasks, "datetime", FrozenDateTime)

        async def allowed(*_args, **_kwargs):
            return CostGuardDecision(allowed=True)

        async def noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allowed)
        monkeypatch.setattr(cost_guard, "reserve", allowed)
        monkeypatch.setattr(cost_guard, "record_provider_call", noop)
        monkeypatch.setattr(cost_guard, "settle_reservation", noop)
        monkeypatch.setattr(provider_usage, "record_attempt", noop)
        monkeypatch.setattr(
            tasks,
            "screen_content_against_philosophy",
            lambda _item, _philosophy: EssenceScreeningResult(
                status=ESSENCE_STATUS_ALIGNED, summary={}
            ),
        )
        monkeypatch.setattr(
            tasks, "prepare_automatic_content_brief_sync", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            tasks,
            "_recover_missing_content_image",
            lambda *_args, **_kwargs: tasks.GenerationItemState.SUCCEEDED,
        )
        monkeypatch.setattr(tasks, "_persist_publication_readiness", lambda *_args: None)

        with factory() as db:
            hospital = db.get(Hospital, hospital_id)
            item = db.get(ContentItem, item_id)
            assert hospital is not None and item is not None
            hospital.director_name = "김예산"
            item.content_type = ContentType.COLUMN
            first_philosophy = HospitalContentPhilosophy(
                hospital_id=hospital_id,
                version=1,
                status=PhilosophyStatus.APPROVED,
                is_base=True,
                positioning_statement="환자의 질문을 차분히 설명합니다.",
                doctor_voice="차분한 설명",
                patient_promise="확인 가능한 내용만 안내합니다.",
                content_principles=[],
                tone_guidelines=[],
                must_use_messages=[],
                avoid_messages=[],
                treatment_narratives=[],
                local_context={},
                medical_ad_risk_rules=[],
                evidence_map={},
                source_asset_ids=[],
                unsupported_gaps=[],
                conflict_notes=[],
                source_snapshot_hash="1" * 64,
            )
            db.add(first_philosophy)
            db.commit()
            first_id = first_philosophy.id

        selected_id = first_id
        monkeypatch.setattr(
            tasks,
            "_generation_philosophy_sync",
            lambda db, *_args: db.get(HospitalContentPhilosophy, selected_id),
        )

        with factory() as first_worker:
            item = first_worker.get(ContentItem, item_id)
            hospital = first_worker.get(Hospital, hospital_id)
            assert item is not None and hospital is not None
            state, code, _message = tasks._generate_single_content_item(
                first_worker, item, hospital
            )
            assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
            assert item.content_philosophy_id == first_id

        assert len(_ProviderHandler.requests) == 2
        FrozenDateTime.current = first_day + timedelta(days=1)
        with factory() as restarted_next_day:
            item = restarted_next_day.get(ContentItem, item_id)
            hospital = restarted_next_day.get(Hospital, hospital_id)
            assert item is not None and hospital is not None
            state, code, _message = tasks._generate_single_content_item(
                restarted_next_day, item, hospital
            )
            assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
        assert len(_ProviderHandler.requests) == 2

        with factory() as db:
            first_philosophy = db.get(HospitalContentPhilosophy, first_id)
            assert first_philosophy is not None
            first_philosophy.status = PhilosophyStatus.ARCHIVED
            first_philosophy.is_base = False
            second_philosophy = HospitalContentPhilosophy(
                hospital_id=hospital_id,
                version=2,
                status=PhilosophyStatus.APPROVED,
                is_base=True,
                positioning_statement="검사 전 준비를 먼저 설명합니다.",
                doctor_voice="간결한 설명",
                patient_promise="최신 승인 기준으로 안내합니다.",
                content_principles=[],
                tone_guidelines=[],
                must_use_messages=[],
                avoid_messages=[],
                treatment_narratives=[],
                local_context={},
                medical_ad_risk_rules=[],
                evidence_map={},
                source_asset_ids=[],
                unsupported_gaps=[],
                conflict_notes=[],
                source_snapshot_hash="2" * 64,
            )
            db.add(second_philosophy)
            db.commit()
            selected_id = second_philosophy.id

        with factory() as regeneration_worker:
            item = regeneration_worker.get(ContentItem, item_id)
            hospital = regeneration_worker.get(Hospital, hospital_id)
            assert item is not None and hospital is not None
            state, code, _message = tasks._generate_single_content_item(
                regeneration_worker, item, hospital
            )
            assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
            assert item.content_philosophy_id == selected_id

        assert len(_ProviderHandler.requests) == 4
        with factory() as verify:
            item = verify.get(ContentItem, item_id)
            assert item is not None
            budget = read_generation_budget(item.essence_check_summary["generation_attempt"])
            assert (budget.writer_results, budget.reviewer_results) == (2, 2)
            assert budget.text_http_attempts == 4
            assert budget.text_http_reserved == 0
    finally:
        _cleanup(factory, hospital_id)


def test_image_transport_ceiling_is_twelve_across_restarts(pg_engine) -> None:
    factory = _factory(pg_engine)
    hospital_id, item_id = _seed(factory)
    try:
        for acquisition in range(4):
            with factory() as db:
                item = db.get(ContentItem, item_id)
                assert item is not None
                ledger = GenerationBudgetLedger(db, item)
                reservation = ledger.reserve(
                    acquisition_id=f"image:{acquisition}", kind="IMAGE"
                )
                for _attempt in range(3):
                    ledger.transport_started(reservation)
                ledger.complete(reservation, transport_attempts=3, outcome="ERROR")

        with factory() as db:
            item = db.get(ContentItem, item_id)
            assert item is not None
            ledger = GenerationBudgetLedger(db, item)
            with pytest.raises(GenerationBudgetExceeded, match="IMAGE_HTTP"):
                ledger.reserve(acquisition_id="image:5", kind="IMAGE")
            assert ledger.snapshot().image_http_attempts == 12
    finally:
        _cleanup(factory, hospital_id)


def test_image_503_exhaustion_keeps_text_publishable(
    pg_engine, monkeypatch, provider_server
) -> None:
    factory = _factory(pg_engine)
    hospital_id, item_id = _seed(factory)

    async def failing_image(*_args, transport_observer, **_kwargs):
        for _attempt in range(3):
            transport_observer()
            try:
                urlopen(Request(f"{provider_server}/image", data=b"{}"), timeout=2)
            except HTTPError as exc:
                assert exc.code == 503
        raise ConnectionError("loopback image transport 503")

    monkeypatch.setattr(tasks, "generate_image", failing_image)
    monkeypatch.setattr(
        content_publication,
        "screen_content_against_philosophy",
        lambda _item, _philosophy: EssenceScreeningResult(
            status=ESSENCE_STATUS_ALIGNED, summary={}
        ),
    )
    with factory() as db:
        philosophy = HospitalContentPhilosophy(
            id=uuid.uuid4(),
            hospital_id=hospital_id,
            version=1,
            status=PhilosophyStatus.APPROVED,
            is_base=True,
            positioning_statement="확인 가능한 내용을 설명합니다.",
            doctor_voice="차분한 설명",
            patient_promise="환자의 질문을 먼저 듣습니다.",
            content_principles=[],
            tone_guidelines=[],
            must_use_messages=[],
            avoid_messages=[],
            treatment_narratives=[],
            local_context={},
            medical_ad_risk_rules=[],
            evidence_map={},
            source_asset_ids=[],
            unsupported_gaps=[],
            conflict_notes=[],
            source_snapshot_hash="a" * 64,
        )
        db.add(philosophy)
        db.commit()
        philosophy_id = philosophy.id
    try:
        monkeypatch.setattr(
            tasks,
            "_generation_philosophy_sync",
            lambda db, *_args: db.get(HospitalContentPhilosophy, philosophy_id),
        )
        monkeypatch.setattr(tasks, "retry_is_due", lambda _attempt: True)
        monkeypatch.setattr(
            tasks,
            "generate_content",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("approved body must not be regenerated for an image failure")
            ),
        )
        for _day in range(4):
            with factory() as db:
                item = db.get(ContentItem, item_id)
                hospital = db.get(Hospital, hospital_id)
                assert item is not None and hospital is not None
                item.title = "건강검진 전 준비"
                item.body = "서울 생성 예산 의원에서 건강검진 전 준비 사항을 안내합니다."
                item.meta_description = "건강검진 준비 안내"
                item.faq_question = "건강검진 전에 금식해야 하나요?"
                item.faq_answer_summary = "검사 종류에 따라 달라 예약 안내를 확인해야 합니다."
                item.references_list = [
                    {"title": "질병관리청", "url": "https://health.kdca.go.kr/example"}
                ]
                item.reference_checks = [
                    {"url": "https://health.kdca.go.kr/example", "verdict": "pass"}
                ]
                item.content_philosophy_id = philosophy_id
                item.content_brief = {
                    "schema_version": "content-brief-v1",
                    "target_query": "건강검진 전 준비",
                    "treatment_narrative": {
                        "source": "hospital_profile",
                        "angle": "검진 준비",
                    },
                    "source_snapshot": {
                        "hash": "b" * 64,
                        "source_asset_ids": [str(uuid.uuid4())],
                    },
                }
                summary = dict(item.essence_check_summary or {})
                summary["generation_provenance"] = {
                    "source_asset_ids": item.content_brief["source_snapshot"][
                        "source_asset_ids"
                    ]
                }
                item.essence_check_summary = summary
                db.commit()
                state, _code, _message = tasks._generate_single_content_item(
                    db, item, hospital
                )
                assert state is tasks.GenerationItemState.PARTIAL

        with factory() as db:
            item = db.get(ContentItem, item_id)
            hospital = db.get(Hospital, hospital_id)
            assert item is not None and hospital is not None
            state, _code, _message = tasks._generate_single_content_item(db, item, hospital)
            assert state is tasks.GenerationItemState.PARTIAL
            assert item.title == "건강검진 전 준비"
            assert item.body is not None and "건강검진 전 준비" in item.body
            assert item.image_url is None
            assert item.essence_check_summary["generation_attempt"]["reason"] == (
                "IMAGE_GENERATION_RETRIES_EXHAUSTED"
            )
            philosophy = db.get(HospitalContentPhilosophy, philosophy_id)
            assert philosophy is not None
            assessment = content_publication.assess_content_publication(item, philosophy)
            assert assessment.publishable is True
            assert len(
                [request for request in _ProviderHandler.requests if request["path"] == "/image"]
            ) == 12

            item.scheduled_date = date.today()
            hospital.site_live = True
            db.commit()

        monkeypatch.setattr(tasks, "SyncSessionLocal", factory)
        monkeypatch.setattr(tasks, "_prefetch_publication_references", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tasks, "publication_references_current", lambda _item: True)
        monkeypatch.setattr(
            tasks, "bind_reference_checks_to_revision", lambda item: item.reference_checks
        )
        monkeypatch.setattr(
            tasks,
            "get_current_approved_philosophy_sync",
            lambda db, *_args: db.get(HospitalContentPhilosophy, philosophy_id),
        )
        monkeypatch.setattr(tasks, "ensure_site_revalidate_configured", lambda: None)
        monkeypatch.setattr(tasks, "enqueue_public_surface_intent", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(tasks.indexnow, "enqueue_content_published_sync", lambda *_args, **_kwargs: None)
        published = tasks._auto_publish_one(item_id, today_kst=date.today())
        assert published is not None

        with factory() as verify:
            item = verify.get(ContentItem, item_id)
            assert item is not None
            assert item.status is ContentStatus.PUBLISHED
            assert item.body is not None and "건강검진 전 준비" in item.body
    finally:
        # The immutable published revision is retained in the dedicated QA lane
        # as verifier evidence; revision deletion is deliberately prohibited.
        pass
