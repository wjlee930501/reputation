"""마감된 달 언급 재판정(PostgreSQL) — 저장 답변을 지금 규칙 대조로 다시 판정하고 그 달의 실제 판정 기준을 남긴다."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from test_monthly_template_refresh_postgres import pg_session  # noqa: F401 — pg_session fixture

from app.models.hospital import Hospital
from app.models.operations import OperationRun
from app.models.sov import QueryMatrix, SovRecord
from app.services import competitor_rejudge, month_mention_rejudge, monthly_manifest, sov_engine

NAME = "가상성모탑정형외과"
RIVAL = "가상바른정형외과의원"
OLD_POLICY = {"judge_prompt_fingerprint": "old-gpt-4o-mini-prompt", "judge_model": "openai/gpt-4o-mini"}


def _seed(session, monkeypatch):
    """옛 판정 정책으로 동결한 10월 측정표 — 공식 등록명 표기를 놓친 기록이 있다."""
    hospital = Hospital(
        name=NAME,
        slug=f"month-rejudge-{uuid.uuid4().hex}",
        region=["가상구", "가상동"],
        address="서울 가상구 큰길로 76",
        competitors=[RIVAL],
    )
    session.add(hospital)
    session.flush()
    query = QueryMatrix(hospital_id=hospital.id, query_text="가상구 정형외과 추천", query_intent="LOCAL")
    session.add(query)
    session.flush()
    monkeypatch.setattr(
        monthly_manifest,
        "_protocol_snapshot",
        lambda kwargs: {**sov_engine.measurement_protocol(**(kwargs or {})), **OLD_POLICY},
    )
    manifest = monthly_manifest.freeze_monthly_manifest(
        session,
        hospital.id,
        2026,
        10,
        [
            monthly_manifest.ManifestCellSpec(
                query_key=f"query:{query.id}",
                query_text=query.query_text,
                platform=platform,
                query_matrix_id=query.id,
                query_target_id=None,
                query_variant_id=None,
                query_intent="LOCAL",
            )
            for platform in ("chatgpt", "gemini")
        ],
        gemini_configured=True,
    )
    missed = SovRecord(
        hospital_id=hospital.id, query_id=query.id, ai_platform="chatgpt", measurement_status="SUCCESS",
        mention_verdict="NOT_MATCHED", is_mentioned=False,
        raw_response="1. **성모탑정형외과의원** — 서울 가상구 큰길로 76\n2. **가상바른정형외과** (가상구)",
        competitor_mentions=[{"name": RIVAL, "is_mentioned": False, "mention_rank": None}],
    )
    ambiguous = SovRecord(
        hospital_id=hospital.id, query_id=query.id, ai_platform="gemini", measurement_status="SUCCESS",
        mention_verdict="AMBIGUOUS", is_mentioned=None,
        raw_response="* **성모탑정형외과의원** (가상구 가상동)",
    )
    session.add_all((missed, ambiguous))
    session.flush()
    cells = {cell.platform: cell for cell in manifest.cells}
    session.add_all(
        [monthly_manifest.link_attempt(cells["chatgpt"], missed), monthly_manifest.link_attempt(cells["gemini"], ambiguous)]
    )
    session.flush()
    return hospital, manifest, missed, ambiguous


def test_rejudge_fixes_missed_mentions_and_records_the_months_policy(pg_session, monkeypatch):  # noqa: F811
    hospital, manifest, missed, ambiguous = _seed(pg_session, monkeypatch)
    assert not competitor_rejudge.month_judge_policy_is_current(manifest, pg_session)

    plan = month_mention_rejudge.build_plan(pg_session, hospital, 2026, 10)
    assert (plan.confirmed_records, plan.self_flips, plan.competitor_changes, plan.already_current) == (1, 1, 1, False)

    month_mention_rejudge.execute(pg_session, hospital, 2026, 10)
    pg_session.flush()
    pg_session.expire_all()

    fixed = pg_session.get(SovRecord, missed.id)
    assert (fixed.mention_verdict, fixed.is_mentioned) == ("MATCHED", True)
    assert fixed.competitor_mentions == [{"name": RIVAL, "is_mentioned": True, "mention_rank": 2}]
    # 확정 여부를 바꾸지 않는다 — 보류 기록은 그대로다.
    untouched = pg_session.get(SovRecord, ambiguous.id)
    assert (untouched.mention_verdict, untouched.is_mentioned) == ("AMBIGUOUS", None)

    run = pg_session.execute(
        select(OperationRun).where(OperationRun.operation_type == month_mention_rejudge.OPERATION_TYPE)
    ).scalar_one()
    assert run.state == "SUCCEEDED" and run.result_summary["self_flips"] == 1
    effective = month_mention_rejudge.effective_measurement_protocol(pg_session, manifest)
    current = sov_engine.measurement_protocol()
    assert effective["judge_prompt_fingerprint"] == current["judge_prompt_fingerprint"]
    assert effective["judge_model"] == current["judge_model"]
    # 측정표의 동결 기준 자체는 그대로다(고칠 수 없다).
    assert manifest.platform_provenance["measurement_protocol"]["judge_prompt_fingerprint"] == OLD_POLICY[
        "judge_prompt_fingerprint"
    ]
    assert competitor_rejudge.month_judge_policy_is_current(manifest, pg_session)


def test_rejudge_is_idempotent_once_the_month_uses_the_current_policy(pg_session, monkeypatch):  # noqa: F811
    hospital, manifest, _missed, _ambiguous = _seed(pg_session, monkeypatch)
    month_mention_rejudge.execute(pg_session, hospital, 2026, 10)
    pg_session.flush()

    again = month_mention_rejudge.execute(pg_session, hospital, 2026, 10)
    pg_session.flush()

    assert again.already_current
    runs = pg_session.execute(
        select(OperationRun).where(OperationRun.operation_type == month_mention_rejudge.OPERATION_TYPE)
    ).scalars().all()
    assert len(runs) == 1


def test_months_without_a_rejudge_keep_their_frozen_protocol(pg_session, monkeypatch):  # noqa: F811
    _hospital, manifest, _missed, _ambiguous = _seed(pg_session, monkeypatch)

    effective = month_mention_rejudge.effective_measurement_protocol(pg_session, manifest)

    assert effective["judge_prompt_fingerprint"] == OLD_POLICY["judge_prompt_fingerprint"]
    assert month_mention_rejudge.effective_measurement_protocol(pg_session, None) is None


def test_duplicate_and_unregistered_competitor_entries_are_rejudged(pg_session, monkeypatch):  # noqa: F811
    hospital, manifest, missed, _ambiguous = _seed(pg_session, monkeypatch)
    # 같은 이름의 중복 항목(보고서는 첫 확정 항목을 쓴다)과 지금은 등록에서 빠진 이름의 옛 판정.
    missed.competitor_mentions = [
        {"name": RIVAL, "is_mentioned": True, "mention_rank": 1},
        {"name": RIVAL, "is_mentioned": False, "mention_rank": None},
        {"name": "가상멀리내과의원", "is_mentioned": True, "mention_rank": 3},
    ]
    hospital.competitors = [RIVAL]
    pg_session.flush()

    month_mention_rejudge.execute(pg_session, hospital, 2026, 10)
    pg_session.flush()
    pg_session.expire_all()

    entries = pg_session.get(SovRecord, missed.id).competitor_mentions
    assert entries == [
        {"name": RIVAL, "is_mentioned": True, "mention_rank": 2},
        # 등록에서 빠진 이름도 같은 규칙으로 다시 판정해 둔다 — 다시 등록돼도 옛 판정이 되살아나지 않는다.
        {"name": "가상멀리내과의원", "is_mentioned": False, "mention_rank": None},
    ]


def test_rejudge_can_return_to_a_previous_judge_policy(pg_session, monkeypatch):  # noqa: F811
    hospital, manifest, _missed, _ambiguous = _seed(pg_session, monkeypatch)
    month_mention_rejudge.execute(pg_session, hospital, 2026, 10)
    pg_session.flush()
    original = month_mention_rejudge.current_judge_policy()
    other = {**original, "judge_model": "anthropic/other-model"}

    monkeypatch.setattr(month_mention_rejudge, "current_judge_policy", lambda: other)
    month_mention_rejudge.execute(pg_session, hospital, 2026, 10)
    pg_session.flush()
    monkeypatch.setattr(month_mention_rejudge, "current_judge_policy", lambda: original)
    month_mention_rejudge.execute(pg_session, hospital, 2026, 10)
    pg_session.flush()

    runs = pg_session.execute(
        select(OperationRun).where(OperationRun.operation_type == month_mention_rejudge.OPERATION_TYPE)
    ).scalars().all()
    assert len(runs) == 3
    assert month_mention_rejudge.month_judge_policy(pg_session, manifest) == original
