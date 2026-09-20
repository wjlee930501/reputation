"""The operator-authorized force path for slots stuck on CONTENT_AI_HARD_FINDING.

2026-09-20 운영 사고: 모델이 HARD로 단정한 사실·의료 안전 지적으로 막힌 슬롯을 야간
복구가 매번 claim하고도 본문이 이미 있어 작가 세션을 사지 않은 채 끝냈다. 여기서
증명하는 것은 두 가지다 — 강제 경로는 본문이 있어도 SKIP하지 않고 실제로 다시 쓴다.
그리고 어떤 경로도 HARD 지적을 건너뛰어 발행하지 못한다.
"""

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.content_ai_review import (
    REVIEW_SCHEMA_VERSION,
    candidate_review_coverage,
    candidate_sha256,
)
from app.services.content_publication import assess_content_publication
from app.services.content_review_feedback import stored_blocking_review_constraints
from app.utils import force_hard_finding_rewrite as force_module
from app.workers import tasks
from app.workers.generation_retry_policy import GenerationRetryClass

HARD_MESSAGE = "간질환 합병증의 안전 관련 단정 표현을 승인 자료에서 확인할 수 없습니다."


class _ForceTaskDB:
    """The narrow session surface `_generate_single_content_item` actually touches."""

    def __init__(self):
        self.commits = 0
        self.added = []

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

        def scalar_one_or_none(self):
            return None

    def execute(self, _statement):
        return self._Result()

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def refresh(self, _item):
        return None

    def expire(self, _item):
        return None


def _hard_blocked_item(philosophy, *, severity="HARD", kind="MEDICAL_SAFETY"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        title="stored title",
        body="stored body",
        image_url=None,
        content_philosophy_id=philosophy.id,
        content_type=SimpleNamespace(value="COLUMN"),
        query_target_id=None,
        scheduled_date=date(2026, 9, 13),
        sequence_no=1,
        status=ContentStatus.DRAFT,
        published_at=None,
        generation_claim_token=None,
        content_revision=1,
        essence_status="NEEDS_ESSENCE_REVIEW",
        essence_check_summary={
            "blocking": True,
            "findings": [HARD_MESSAGE],
            "ai_review": {
                "status": "REVISE",
                "blocking": True,
                "schema_version": REVIEW_SCHEMA_VERSION,
                "findings": [
                    {"severity": severity, "kind": kind, "message": HARD_MESSAGE},
                    {"severity": "SOFT", "kind": "STYLE", "message": "문장을 다듬으세요."},
                ],
            },
        },
    )


def _writer(calls, *, summary=None):
    async def regenerate(**kwargs):
        calls.append(kwargs)
        return (
            {
                "title": "rewritten title",
                "body": "rewritten body",
                "meta_description": "summary",
                "references": [],
                "faq_question": None,
                "faq_answer_summary": None,
            },
            SimpleNamespace(
                status="ALIGNED",
                summary=summary if summary is not None else {"blocking": False},
            ),
        )

    return regenerate


def _write_back(item):
    def write_content(_db, **kwargs):
        for field, value in kwargs["values"].items():
            setattr(item, field, value)
        return 1

    return write_content


def _allow_cost(monkeypatch):
    async def allowed(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allowed)


def test_forced_rewrite_regenerates_a_stored_hard_blocked_body(monkeypatch):
    """본문이 있다는 사실이 강제 경로에서는 SKIP 사유가 아니다."""

    philosophy = SimpleNamespace(id=uuid.uuid4())
    item = _hard_blocked_item(philosophy)
    hospital = SimpleNamespace(id=item.hospital_id, name="세브란스W내과", slug="w-internal")
    db = _ForceTaskDB()
    writer_calls: list[dict] = []
    # 저장된 차단 → 재작성 → 재검수 통과. 마지막 판정만 발행 가능이다.
    assessments = iter(["CONTENT_AI_HARD_FINDING", None, None])

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "assess_content_publication",
        lambda *_args: SimpleNamespace(
            code=next(assessments), message="독립 검수 지적이 남아 있습니다."
        ),
    )
    _allow_cost(monkeypatch)
    monkeypatch.setattr(
        tasks, "prepare_automatic_content_brief_sync", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(tasks, "_generate_with_auto_review", _writer(writer_calls))
    monkeypatch.setattr(tasks, "_generation_summary", lambda *_args: _args[2].summary)
    monkeypatch.setattr(tasks, "write_back_generated_content", _write_back(item))
    monkeypatch.setattr(
        tasks, "_recover_missing_content_image", lambda *_args: tasks.GenerationItemState.SUCCEEDED
    )
    monkeypatch.setattr(tasks, "_persist_publication_readiness", lambda *_args: None)

    state, code, _message = tasks._generate_single_content_item(
        db, item, hospital, force_hard_rewrite=True
    )

    assert state == tasks.GenerationItemState.SUCCEEDED
    assert code is None
    assert len(writer_calls) == 1, "강제 경로는 작가 세션을 정확히 한 번 산다"
    constraints = writer_calls[0]["rewrite_constraints"]
    assert HARD_MESSAGE in constraints, "HARD 지적이 재작성 제약으로 넘어가야 한다"
    assert any("삭제" in line for line in constraints), "삭제·완화 지시문이 함께 넘어가야 한다"
    assert item.body == "rewritten body"


def test_forced_rewrite_that_stays_hard_leaves_the_slot_blocked(monkeypatch):
    """재작성 뒤에도 사실·안전 지적이 남으면 그대로 차단이다. 이미지 예산도 쓰지 않는다."""

    philosophy = SimpleNamespace(id=uuid.uuid4())
    item = _hard_blocked_item(philosophy)
    hospital = SimpleNamespace(id=item.hospital_id, name="행복드림의원", slug="dream")
    db = _ForceTaskDB()
    writer_calls: list[dict] = []

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "assess_content_publication",
        lambda *_args: SimpleNamespace(
            publishable=False,
            code="CONTENT_AI_HARD_FINDING",
            message="승인된 병원 자료 또는 의료 근거의 보완이 필요합니다.",
            violations=(),
            essence_status="NEEDS_ESSENCE_REVIEW",
            essence_summary={"blocking": True, "findings": [HARD_MESSAGE]},
            philosophy_id=philosophy.id,
        ),
    )
    _allow_cost(monkeypatch)
    monkeypatch.setattr(
        tasks, "prepare_automatic_content_brief_sync", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        tasks,
        "_generate_with_auto_review",
        _writer(
            writer_calls,
            summary={
                "blocking": True,
                "findings": [HARD_MESSAGE],
                "ai_review": {
                    "status": "REVISE",
                    "blocking": True,
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "findings": [
                        {
                            "severity": "HARD",
                            "kind": "MEDICAL_SAFETY",
                            "message": HARD_MESSAGE,
                        }
                    ],
                },
            },
        ),
    )
    monkeypatch.setattr(tasks, "_generation_summary", lambda *_args: _args[2].summary)
    monkeypatch.setattr(tasks, "write_back_generated_content", _write_back(item))
    monkeypatch.setattr(
        tasks,
        "_recover_missing_content_image",
        lambda *_args: pytest.fail("a surviving hard finding must not spend image budget"),
    )

    state, code, message = tasks._generate_single_content_item(
        db, item, hospital, force_hard_rewrite=True
    )

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    assert "근거" in message
    assert len(writer_calls) == 1
    assert item.status == ContentStatus.DRAFT, "차단된 슬롯은 발행 상태로 가지 않는다"
    assert tasks._stored_generation_attempt(item)["reason"] == "CONTENT_AI_HARD_FINDING"


def test_scheduled_sweep_still_fails_closed_without_the_force_flag(monkeypatch):
    """예약 스윕의 계약은 그대로다 — 사람의 결정 없이는 작가 세션을 사지 않는다."""

    philosophy = SimpleNamespace(id=uuid.uuid4())
    item = _hard_blocked_item(philosophy)
    hospital = SimpleNamespace(id=item.hospital_id, name="마포성모탑", slug="mapo")

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "assess_content_publication",
        lambda *_args: SimpleNamespace(
            code="CONTENT_AI_HARD_FINDING", message="승인 자료의 보완이 필요합니다."
        ),
    )
    monkeypatch.setattr(
        tasks,
        "_generate_with_auto_review",
        lambda **_kwargs: pytest.fail("a scheduled sweep must not rewrite a hard finding"),
    )
    monkeypatch.setattr(
        tasks,
        "review_generated_content",
        lambda **_kwargs: pytest.fail("a model-declared hard finding buys no new review"),
    )

    state, code, _message = tasks._generate_single_content_item(
        _ForceTaskDB(), item, hospital
    )

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")


def test_forced_rewrite_is_not_skipped_by_an_unchanged_attempt_record(monkeypatch):
    """claim 후 0.3초 SKIP이 강제 경로의 결과가 되어서는 안 된다."""

    philosophy = SimpleNamespace(id=uuid.uuid4())
    item = _hard_blocked_item(philosophy)
    hospital = SimpleNamespace(id=item.hospital_id, name="강심장정형외과", slug="gangsim")
    db = _ForceTaskDB()
    writer_calls: list[dict] = []
    assessments = iter(["CONTENT_AI_HARD_FINDING", None, None])

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    tasks._remember_generation_attempt(db, item, philosophy, "CONTENT_AI_HARD_FINDING")
    stored = tasks._stored_generation_attempt(item)
    assert stored["retry_class"] == GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
    assert tasks._generation_attempt_is_unchanged(item, philosophy) is True

    monkeypatch.setattr(
        tasks,
        "assess_content_publication",
        lambda *_args: SimpleNamespace(
            code=next(assessments), message="독립 검수 지적이 남아 있습니다."
        ),
    )
    _allow_cost(monkeypatch)
    monkeypatch.setattr(
        tasks, "prepare_automatic_content_brief_sync", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(tasks, "_generate_with_auto_review", _writer(writer_calls))
    monkeypatch.setattr(tasks, "_generation_summary", lambda *_args: _args[2].summary)
    monkeypatch.setattr(tasks, "write_back_generated_content", _write_back(item))
    monkeypatch.setattr(
        tasks, "_recover_missing_content_image", lambda *_args: tasks.GenerationItemState.SUCCEEDED
    )
    monkeypatch.setattr(tasks, "_persist_publication_readiness", lambda *_args: None)

    state, _code, _message = tasks._generate_single_content_item(
        db, item, hospital, force_hard_rewrite=True
    )

    assert state != tasks.GenerationItemState.SKIPPED
    assert len(writer_calls) == 1


def test_a_review_outage_keeps_its_own_re_review_path(monkeypatch):
    """UNAVAILABLE는 종전대로 같은 본문을 다시 검수한다 — 강제 경로가 가로채지 않는다."""

    philosophy = SimpleNamespace(id=uuid.uuid4())
    item = _hard_blocked_item(philosophy)
    item.essence_check_summary["ai_review"]["status"] = "UNAVAILABLE"
    hospital = SimpleNamespace(id=item.hospital_id, name="장앤김내과", slug="jangkim")
    db = _ForceTaskDB()
    reviewed: list[str] = []
    assessments = iter(["CONTENT_AI_REVIEW_UNAVAILABLE", None, None])

    async def reviewer(**kwargs):
        reviewed.append(kwargs["content"]["title"])
        return SimpleNamespace(
            status=tasks.ContentAiReviewStatus.PASS,
            payload=lambda: {"status": "PASS", "blocking": False},
        )

    monkeypatch.setattr(tasks, "_generation_philosophy_sync", lambda *_args: philosophy)
    monkeypatch.setattr(
        tasks,
        "assess_content_publication",
        lambda *_args: SimpleNamespace(
            code=next(assessments), message="독립 검수를 완료하지 못했습니다."
        ),
    )
    monkeypatch.setattr(tasks, "review_generated_content", reviewer)
    monkeypatch.setattr(
        tasks,
        "_generate_with_auto_review",
        lambda **_kwargs: pytest.fail("a review outage must not spend a writer session"),
    )
    monkeypatch.setattr(
        tasks, "_recover_missing_content_image", lambda *_args: tasks.GenerationItemState.SUCCEEDED
    )
    monkeypatch.setattr(tasks, "_persist_publication_readiness", lambda *_args: None)

    state, code, _message = tasks._generate_single_content_item(db, item, hospital)

    assert reviewed == ["stored title"], "저장된 본문을 그대로 재검수한다"
    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)


def _publication_probe_item() -> ContentItem:
    item = ContentItem(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        content_type=ContentType.NOTICE,
        status=ContentStatus.DRAFT,
        title="간질환 관리 안내",
        body="## 안내\n\n간질환 관리에 대한 일반적인 정보입니다." * 20,
        meta_description="간질환 관리 안내",
        references_list=[],
        image_url="https://example.test/image.png",
        image_policy_verified_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    item.essence_check_summary = {
        "ai_review": {
            "status": "REVISE",
            "blocking": True,
            "schema_version": REVIEW_SCHEMA_VERSION,
            "findings": [
                {"severity": "HARD", "kind": "MEDICAL_SAFETY", "message": HARD_MESSAGE}
            ],
            "candidate_sha256": candidate_sha256(item),
            "coverage": candidate_review_coverage(item),
        }
    }
    return item


def test_a_stored_hard_finding_can_never_be_skipped_to_publish():
    """발행 게이트는 강제 경로와 무관하다 — HARD 지적이 남은 글은 발행 불가다."""

    item = _publication_probe_item()

    assessment = assess_content_publication(item, None)

    assert assessment.publishable is False
    assert assessment.code == "CONTENT_AI_HARD_FINDING"
    assert HARD_MESSAGE in assessment.message


def test_stored_hard_findings_become_removal_constraints():
    summary = {
        "ai_review": {
            "findings": [
                {"severity": "HARD", "kind": "HOSPITAL_FACT", "message": "장비 근거 없음"},
                {"severity": "UNCERTAIN", "kind": "MEDICAL_SAFETY", "message": "재검수 필요"},
                {"severity": "SOFT", "kind": "STYLE", "message": "문체"},
            ]
        }
    }

    constraints = stored_blocking_review_constraints(summary)

    assert "장비 근거 없음" in constraints
    assert "재검수 필요" in constraints
    assert "문체" not in constraints, "비차단 지적은 강제 재작성의 제약이 아니다"
    assert any("삭제" in line for line in constraints)


def test_constraints_without_a_hard_finding_carry_no_removal_instruction():
    summary = {
        "ai_review": {
            "findings": [
                {"severity": "UNCERTAIN", "kind": "MEDICAL_SAFETY", "message": "재검수 필요"}
            ]
        }
    }

    assert stored_blocking_review_constraints(summary) == ["재검수 필요"]
    assert stored_blocking_review_constraints({}) == []
    assert stored_blocking_review_constraints(None) == []


def _plan_item(scheduled_date: date, sequence_no: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        scheduled_date=scheduled_date,
        sequence_no=sequence_no,
        status=ContentStatus.DRAFT,
    )


def test_forced_rewrites_are_planned_in_publication_order():
    """가장 오래 밀린 슬롯이 먼저 큐에 들어간다 — 09-13이 09-15·09-16보다 앞이다."""

    late = _plan_item(date(2026, 9, 16), 1)
    middle = _plan_item(date(2026, 9, 15), 1)
    first = _plan_item(date(2026, 9, 13), 1)
    second = _plan_item(date(2026, 9, 13), 2)

    plan = force_module.plan_forced_hard_rewrites(
        [late, second, middle, first],
        block_code_of=lambda _item: "CONTENT_AI_HARD_FINDING",
    )

    assert [target.content_id for target in plan.targets] == [
        first.id,
        second.id,
        middle.id,
        late.id,
    ]
    assert plan.refused == []


def test_the_planner_refuses_slots_the_force_path_does_not_own():
    hard = _plan_item(date(2026, 9, 13), 1)
    other_block = _plan_item(date(2026, 9, 13), 2)
    publishable = _plan_item(date(2026, 9, 13), 3)
    published = _plan_item(date(2026, 9, 13), 4)
    published.status = ContentStatus.PUBLISHED
    codes = {
        hard.id: "CONTENT_AI_HARD_FINDING",
        other_block.id: "CONTENT_IMAGE_NOT_READY",
        publishable.id: None,
        published.id: "CONTENT_AI_HARD_FINDING",
    }

    plan = force_module.plan_forced_hard_rewrites(
        [hard, other_block, publishable, published],
        block_code_of=lambda item: codes[item.id],
    )

    assert [target.content_id for target in plan.targets] == [hard.id]
    assert {content_id for content_id, _reason in plan.refused} == {
        str(other_block.id),
        str(publishable.id),
        str(published.id),
    }


def test_the_dry_run_enqueues_nothing(monkeypatch):
    item = _plan_item(date(2026, 9, 13), 1)
    enqueued: list[uuid.UUID] = []

    monkeypatch.setattr(
        force_module,
        "SyncSessionLocal",
        lambda: _FakeSession([item]),
    )
    monkeypatch.setattr(
        force_module, "_stored_block_code", lambda _db, _item: "CONTENT_AI_HARD_FINDING"
    )

    result = force_module.force_hard_finding_rewrites(
        [item.id], enqueue=lambda target: enqueued.append(target.content_id)
    )

    assert [target.content_id for target in result.targets] == [item.id]
    assert result.enqueued == []
    assert enqueued == []

    applied = force_module.force_hard_finding_rewrites(
        [item.id], apply_changes=True, enqueue=lambda target: enqueued.append(target.content_id)
    )

    assert applied.enqueued == [item.id]
    assert enqueued == [item.id]


def test_unknown_content_ids_are_refused_by_name(monkeypatch):
    missing = uuid.uuid4()
    monkeypatch.setattr(force_module, "SyncSessionLocal", lambda: _FakeSession([]))

    result = force_module.force_hard_finding_rewrites([missing], apply_changes=True)

    assert result.targets == []
    assert result.enqueued == []
    assert result.refused == [(str(missing), "content item not found")]


def test_content_ids_come_from_argv_or_one_environment_value(monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()

    assert force_module.parse_content_ids([str(first), str(first)]) == [first]

    monkeypatch.setenv(force_module.CONTENT_IDS_ENV, f"{first}, {second}")
    assert force_module.parse_content_ids([]) == [first, second]


class _FakeSession:
    """A read-only stand-in that proves this entry point commits nothing."""

    def __init__(self, items):
        self._items = items
        self.commits = 0
        self.rollbacks = 0

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return self

        def all(self):
            return self._items

    def execute(self, _statement):
        return self._Result(self._items)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False
