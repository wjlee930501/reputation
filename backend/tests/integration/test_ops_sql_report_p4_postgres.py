"""Phase 4 사전 조회(q12~q14)가 실제 PostgreSQL에서 돌고, q12의 술어가 Python 술어와 같은 답을 낸다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.handoff import HandoffSource, HandoffState, HospitalHandoff
from app.models.hospital import Hospital, HospitalStatus
from app.services.hospital_lifecycle import missing_profile_requirement_keys
from app.utils import ops_sql_report


def _hospital(**overrides) -> Hospital:
    values = dict(
        id=uuid.uuid4(),
        name=f"P4조회-{uuid.uuid4().hex[:6]}",
        slug=f"p4q-{uuid.uuid4().hex[:10]}",
        status=HospitalStatus.ONBOARDING,
        profile_complete=False,
        director_name="김원장",
        director_career="외과 전문의",
        director_philosophy=None,
        address="서울 성동구",
        phone="02-000-0000",
        business_hours={"mon": "09:00-18:00"},
        website_url="https://clinic.example.com",
        naver_place_url="https://m.place.naver.com/hospital/123/home",
        google_maps_url=None,
        latitude=37.5,
        longitude=127.0,
        region=["성동구"],
        specialties=["외과"],
        keywords=["치질"],
        competitors=[],
        treatments=[{"name": "치질 수술"}],
    )
    values.update(overrides)
    return Hospital(**values)


def _old_missing(hospital: Hospital) -> list[str]:
    """D31 이전(플래그 꺼짐) 술어 — 약력·철학·네이버+Google 필수."""

    missing = list(missing_profile_requirement_keys(hospital))
    order = [
        "director_basic", "director_philosophy", "contact", "web_channels",
        "ai_channels", "geo", "targeting", "treatments",
    ]
    if not (hospital.director_philosophy or "").strip():
        missing.append("director_philosophy")
    has_google = (hospital.google_maps_url or "").strip() or (
        hospital.google_business_profile_url or ""
    ).strip()
    if not has_google and "ai_channels" not in missing:
        missing.append("ai_channels")
    return [key for key in order if key in missing]


@pytest.fixture
def session(pg_conn):
    with Session(bind=pg_conn, join_transaction_mode="create_savepoint") as db:
        yield db


def _accept(db: Session, hospital: Hospital) -> None:
    db.add(
        HospitalHandoff(
            hospital_id=hospital.id,
            state=HandoffState.HANDOFF_ACCEPTED,
            acceptance_source=HandoffSource.LEGACY_BACKFILL,
            accepted_at=datetime.now(UTC),
        )
    )


def test_q12_matches_the_python_predicates(session) -> None:
    cases = {
        "ready": _hospital(),
        "no_career": _hospital(director_career="  "),
        "google_only": _hospital(naver_place_url=None, google_maps_url="https://maps.google.com/x"),
        "with_google": _hospital(
            director_philosophy="충분히 설명합니다.", google_maps_url="https://maps.google.com/x"
        ),
        "blank_hours": _hospital(business_hours={"mon": "  ", "tue": None}),
        "no_coords": _hospital(latitude=None),
        "blank_treatment": _hospital(treatments=[{"name": " "}, "  "]),
        "string_treatment": _hospital(treatments=["치질 수술"]),
        "paused": _hospital(status=HospitalStatus.PAUSED),
        "not_accepted": _hospital(),
        "complete": _hospital(profile_complete=True),
    }
    for key, hospital in cases.items():
        session.add(hospital)
        session.flush()
        if key != "not_accepted":
            _accept(session, hospital)
    session.flush()

    [sql] = ops_sql_report.QUERIES["q12"]
    rows = {row["id"]: row for row in session.execute(text(sql)).mappings().all()}

    for key, hospital in cases.items():
        row = rows[hospital.id]
        assert list(row["missing_new"]) == missing_profile_requirement_keys(hospital), key
        assert list(row["missing_old"]) == _old_missing(hospital), key

    promote = {key for key, hospital in cases.items() if rows[hospital.id]["would_promote"]}
    assert promote == {"ready", "with_google", "string_treatment"}
    assert rows[cases["not_accepted"].id]["handoff_accepted"] is False
    assert rows[cases["ready"].id]["missing_old"] == ["director_philosophy", "ai_channels"]


def test_q12_predicts_actual_public_activation_not_just_completion(session) -> None:
    cases = {
        "ready": _hospital(),
        "custom_domain": _hospital(aeo_domain="clinic.example.com"),
        "pending_domain": _hospital(status=HospitalStatus.PENDING_DOMAIN, aeo_domain="c.example"),
        "already_live": _hospital(
            status=HospitalStatus.ACTIVE, site_live=True, site_built=True, profile_complete=True
        ),
        "active_not_live": _hospital(status=HospitalStatus.ACTIVE, site_live=False),
        "paused": _hospital(status=HospitalStatus.PAUSED),
        "not_accepted": _hospital(),
        "incomplete": _hospital(director_career=None),
        "complete_not_built": _hospital(profile_complete=True, site_built=False),
    }
    for key, hospital in cases.items():
        session.add(hospital)
        session.flush()
        if key != "not_accepted":
            _accept(session, hospital)
    session.flush()

    [sql] = ops_sql_report.QUERIES["q12"]
    rows = {row["id"]: row for row in session.execute(text(sql)).mappings().all()}
    publish = {key for key, hospital in cases.items() if rows[hospital.id]["would_publish_now"]}
    blockers = {key: set(rows[hospital.id]["activation_blockers"]) for key, hospital in cases.items()}

    # 승격 후보(active_not_live 포함)와 실제 새 공개는 다르다.
    assert rows[cases["active_not_live"].id]["would_promote"] is True
    assert rows[cases["custom_domain"].id]["would_promote"] is True
    assert publish == {"ready"}
    assert rows[cases["custom_domain"].id]["aeo_domain"] == "clinic.example.com"
    assert blockers["ready"] == set()
    assert blockers["custom_domain"] == {"CUSTOM_DOMAIN_PENDING"}
    assert "CUSTOM_DOMAIN_PENDING" in blockers["pending_domain"]
    assert blockers["already_live"] == {"ALREADY_LIVE"}
    assert blockers["active_not_live"] == {"ACTIVE_NOT_LIVE"}
    assert blockers["paused"] == {"PAUSED"}
    assert blockers["not_accepted"] == {"HANDOFF_NOT_ACCEPTED"}
    assert blockers["incomplete"] == {"PROFILE_INCOMPLETE"}
    assert blockers["complete_not_built"] == {"SITE_NOT_BUILT"}


def test_q13_and_q14_run_on_the_migrated_schema(session) -> None:
    hospital = _hospital()
    session.add(hospital)
    session.flush()

    for name in ("q13", "q14"):
        for sql in ops_sql_report.QUERIES[name]:
            session.execute(text(sql)).mappings().all()

    [q13] = ops_sql_report.QUERIES["q13"]
    row = next(
        item for item in session.execute(text(q13)).mappings().all() if item["id"] == hospital.id
    )
    assert row["has_approved_base"] is False
    assert row["autofill_runs"] == 0
    assert row["sources_by_type_status"] == {}
