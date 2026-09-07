from __future__ import annotations

import copy
import json
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.models.content import ContentStatus, ContentType
from app.services.content_ai_review import candidate_review_coverage, candidate_sha256
from app.utils import repair_legacy_faq_questions as repair


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.added: list[object] = []
        self.statements: list[object] = []

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def add(self, value: object) -> None:
        self.added.append(value)

    def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(rowcount=1)


def _row(target: repair.AllowedRepair, index: int):
    hospital_id = uuid.UUID(int=index + 1)
    item = SimpleNamespace(
        id=uuid.UUID(target.content_id),
        hospital_id=hospital_id,
        content_type=ContentType.FAQ,
        status=ContentStatus.PUBLISHED,
        content_revision=7 + index,
        title=f"공개 FAQ {index}",
        body=f"공개 본문 {index}",
        meta_description=f"공개 설명 {index}",
        faq_question=target.old_question,
        faq_answer_summary=f"공개 답변 {index}",
        references_list=[{"title": "질병관리청", "url": "https://www.kdca.go.kr"}],
        essence_check_summary=None,
    )
    hospital = SimpleNamespace(id=hospital_id, slug=target.hospital_slug)
    return item, hospital


def _rows():
    rows = {
        target.content_id: _row(target, index)
        for index, target in enumerate(repair.ALLOWED_REPAIRS)
    }
    first = rows[repair.ALLOWED_REPAIRS[0].content_id][0]
    first.essence_check_summary = {
        "ai_review": {
            "schema_version": 2,
            "status": "PASS",
            "candidate_sha256": candidate_sha256(first),
            "coverage": candidate_review_coverage(first),
            "findings": [],
        }
    }
    second = rows[repair.ALLOWED_REPAIRS[1].content_id][0]
    second.essence_check_summary = {
        "blocking": True,
        "findings": ["기존 미해결 의료 안전 지적"],
        "ai_review": {
            "schema_version": 2,
            "status": "REVISE",
            "candidate_sha256": candidate_sha256(second),
            "coverage": candidate_review_coverage(second),
            "findings": [{"message": "기존 미해결 의료 안전 지적"}],
        },
    }
    return rows


def _plan(monkeypatch, rows=None):
    rows = rows or _rows()
    monkeypatch.setattr(repair, "_load_rows", lambda _db, *, lock: rows)
    return repair.create_repair_plan(FakeSession()), rows


def test_allowlist_is_exact_and_repair_is_one_punctuation_character() -> None:
    assert [(target.content_id, target.hospital_slug, target.old_question) for target in repair.ALLOWED_REPAIRS] == [
        (
            "e4451df7-c392-4a56-b4c7-8a7fe32788b5",
            "haengbogdeurimyiweon",
            "예방접종 초기 증상이 뭔지 알려줘",
        ),
        (
            "f11800fb-d2f4-4821-95ee-7774c702abb6",
            "noweontab365yiweon",
            "상계동에서 도수치료 받을 수 있는 병원 추천해줘",
        ),
        (
            "359a77f7-c019-44c5-93e6-2c8bdd145fda",
            "noweontab365yiweon",
            "노원구 응급의학과 병원 어디가 좋은지 비교해줘",
        ),
    ]
    for target in repair.ALLOWED_REPAIRS:
        assert target.new_question == target.old_question + "?"
        assert len(target.new_question) == len(target.old_question) + 1


def test_dry_run_binds_revision_status_and_hash_without_leaking_candidate_content(
    monkeypatch,
) -> None:
    plan, rows = _plan(monkeypatch)

    assert len(plan.entries) == 3
    assert plan.require_complete_allowlist is True
    assert plan.review_state_after_apply == "DEFERRED"
    assert plan.public_ready_after_apply is False
    assert plan.entries[0].ai_review_was_current is True
    assert plan.entries[0].review_disposition == (
        "CURRENT_REVIEW_WILL_BECOME_STALE_REVIEW_DEFERRED"
    )
    assert plan.entries[1].unresolved_review_preserved is True
    assert plan.entries[1].review_disposition == (
        "EXISTING_UNRESOLVED_REVIEW_PRESERVED_REVIEW_DEFERRED"
    )
    assert plan.entries[2].review_disposition == "NO_CURRENT_REVIEW_REVIEW_DEFERRED"

    serialized = json.dumps(plan.to_dict(), ensure_ascii=False)
    for target in repair.ALLOWED_REPAIRS:
        item = rows[target.content_id][0]
        assert target.content_id in serialized
        assert target.old_question in serialized
        assert item.body not in serialized
        assert item.faq_answer_summary not in serialized
        assert "질병관리청" not in serialized


def test_dry_run_makes_no_mutation_commit_or_provider_call(monkeypatch) -> None:
    rows = _rows()
    before = {
        content_id: copy.deepcopy(vars(item))
        for content_id, (item, _hospital) in rows.items()
    }
    session = FakeSession()
    monkeypatch.setattr(repair, "_load_rows", lambda _db, *, lock: rows)

    repair.create_repair_plan(session)

    assert session.commits == 0
    assert session.rollbacks == 0
    assert {
        content_id: vars(item) for content_id, (item, _hospital) in rows.items()
    } == before


def test_dry_run_rejects_nonpublished_empty_answer_and_changed_question(monkeypatch) -> None:
    mutators = (
        lambda item: setattr(item, "status", ContentStatus.DRAFT),
        lambda item: setattr(item, "faq_answer_summary", ""),
        lambda item: setattr(item, "faq_question", f"{item.faq_question}?"),
    )
    for mutate in mutators:
        rows = _rows()
        mutate(rows[repair.ALLOWED_REPAIRS[0].content_id][0])
        monkeypatch.setattr(repair, "_load_rows", lambda _db, *, lock, rows=rows: rows)
        with pytest.raises(repair.RepairSafetyError):
            repair.create_repair_plan(FakeSession())


def test_apply_changes_only_question_and_revision_and_preserves_review(monkeypatch) -> None:
    plan, rows = _plan(monkeypatch)
    session = FakeSession()
    before = {
        content_id: copy.deepcopy(vars(item))
        for content_id, (item, _hospital) in rows.items()
    }
    audit_details: list[dict] = []

    def cas(_db, entry):
        item = rows[entry.content_id][0]
        item.faq_question = entry.new_question
        item.content_revision = entry.expected_revision + 1
        return 1

    monkeypatch.setattr(repair, "_cas_update", cas)
    monkeypatch.setattr(
        repair,
        "write_audit_log_sync",
        lambda _db, **kwargs: audit_details.append(kwargs),
    )

    result = repair.apply_repair_plan(session, plan)

    assert result.repair_state == "APPLIED_REVIEW_DEFERRED"
    assert result.updated_count == 3
    assert result.review_state == "DEFERRED"
    assert result.public_ready is False
    assert session.commits == 1
    assert session.rollbacks == 0
    assert len(audit_details) == 3
    for target in repair.ALLOWED_REPAIRS:
        item = rows[target.content_id][0]
        changed = {
            key for key, value in vars(item).items() if value != before[target.content_id][key]
        }
        assert changed == {"faq_question", "content_revision"}
        assert item.essence_check_summary == before[target.content_id]["essence_check_summary"]


def test_apply_rejects_plan_tampering_before_reading_rows(monkeypatch) -> None:
    plan, _rows_by_id = _plan(monkeypatch)
    bad_entry = replace(plan.entries[0], new_question="전혀 다른 질문?")
    bad_plan = replace(plan, entries=(bad_entry, *plan.entries[1:]))
    monkeypatch.setattr(
        repair,
        "_load_rows",
        lambda *_args, **_kwargs: pytest.fail("invalid plan must fail before DB read"),
    )

    with pytest.raises(repair.RepairSafetyError, match="broadens allowlist"):
        repair.apply_repair_plan(FakeSession(), bad_plan)


def test_apply_rejects_stale_revision_or_candidate_and_rolls_back(monkeypatch) -> None:
    plan, rows = _plan(monkeypatch)
    rows[plan.entries[0].content_id][0].content_revision += 1
    session = FakeSession()
    monkeypatch.setattr(
        repair, "_cas_update", lambda *_args, **_kwargs: pytest.fail("CAS must not start")
    )

    with pytest.raises(repair.RepairSafetyError, match="no changes applied"):
        repair.apply_repair_plan(session, plan)

    assert session.commits == 0
    assert session.rollbacks == 1


def test_apply_rolls_back_everything_when_any_cas_loses(monkeypatch) -> None:
    plan, _rows_by_id = _plan(monkeypatch)
    session = FakeSession()
    attempts = iter((1, 1, 0))
    monkeypatch.setattr(repair, "_cas_update", lambda *_args, **_kwargs: next(attempts))
    monkeypatch.setattr(repair, "write_audit_log_sync", lambda *_args, **_kwargs: None)

    with pytest.raises(repair.RepairSafetyError, match="compare-and-swap failed"):
        repair.apply_repair_plan(session, plan)

    assert session.commits == 0
    assert session.rollbacks == 1


def test_reapplying_exact_completed_plan_is_idempotent(monkeypatch) -> None:
    plan, rows = _plan(monkeypatch)
    for entry in plan.entries:
        item = rows[entry.content_id][0]
        item.faq_question = entry.new_question
        item.content_revision = entry.expected_revision + 1
    session = FakeSession()
    monkeypatch.setattr(
        repair, "_cas_update", lambda *_args, **_kwargs: pytest.fail("must not update twice")
    )

    result = repair.apply_repair_plan(session, plan)

    assert result.repair_state == "ALREADY_APPLIED_REVIEW_DEFERRED"
    assert result.updated_count == 0
    assert result.public_ready is False
    assert session.commits == 0
    assert session.rollbacks == 1


def test_cas_statement_writes_only_question_and_revision() -> None:
    target = repair.ALLOWED_REPAIRS[0]
    entry = repair.RepairPlanEntry(
        content_id=target.content_id,
        hospital_id=str(uuid.uuid4()),
        hospital_slug=target.hospital_slug,
        old_question=target.old_question,
        new_question=target.new_question,
        expected_revision=11,
        expected_status="PUBLISHED",
        candidate_sha256_before="a" * 64,
        candidate_sha256_after="b" * 64,
        ai_review_status_before=None,
        ai_review_candidate_sha256_before=None,
        ai_review_was_current=False,
        unresolved_review_preserved=False,
        review_disposition="NO_CURRENT_REVIEW_REVIEW_DEFERRED",
    )
    session = FakeSession()

    assert repair._cas_update(session, entry) == 1
    statement = session.statements[0]
    assert {column.name for column in statement._values} == {
        "faq_question",
        "content_revision",
    }
    where = str(statement.whereclause)
    for column in (
        "content_items.id",
        "content_items.hospital_id",
        "content_items.content_type",
        "content_items.status",
        "content_items.faq_question",
        "content_items.content_revision",
    ):
        assert column in where


def test_plan_round_trip_is_utf8_and_existing_plan_is_not_overwritten(
    monkeypatch, tmp_path
) -> None:
    plan, _rows_by_id = _plan(monkeypatch)
    path = tmp_path / "faq-repair-plan.json"

    repair.write_plan(path, plan)

    assert repair.load_plan(path) == plan
    assert "예방접종" in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        repair.write_plan(path, plan)
