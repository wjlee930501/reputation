"""Deterministic local demo seed for browser E2E."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import arrow
from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.essence import (
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus, Plan
from app.models.report import MonthlyReport
from app.models.sov import QueryMatrix, SovRecord
from app.services.essence_engine import (
    build_monthly_essence_summary,
    compute_source_content_hash,
    process_source_asset,
    screen_content_against_philosophy,
    synthesize_philosophy,
)
from app.services.report_engine import generate_pdf_report

DEMO_SLUG = "jangpyeonhan-surgery-demo"


def seed_demo() -> dict[str, str]:
    with SyncSessionLocal() as db:
        existing = db.execute(select(Hospital).where(Hospital.slug == DEMO_SLUG)).scalar_one_or_none()
        if existing:
            db.delete(existing)
            db.commit()

        hospital = Hospital(
            name="장편한외과의원 데모",
            slug=DEMO_SLUG,
            status=HospitalStatus.ACTIVE,
            plan=Plan.PLAN_16,
            address="서울시 강남구 논현로 147길 12",
            phone="02-555-7890",
            business_hours={
                "mon": "09:00-18:00",
                "tue": "09:00-18:00",
                "wed": "09:00-18:00",
                "thu": "09:00-18:00",
                "fri": "09:00-18:00",
                "sat": "09:00-13:00",
                "sun": "휴진",
            },
            website_url="https://jangpyeonhan.example.com",
            blog_url="https://blog.naver.com/jangpyeonhan-demo",
            kakao_channel_url="https://pf.kakao.com/_demo",
            google_business_profile_url="https://business.google.com/example/jangpyeonhan",
            google_maps_url="https://maps.google.com/?cid=1234567890",
            naver_place_url="https://naver.me/demo",
            aeo_domain="ai.jangpyeonhan.co.kr",
            latitude=37.517236,
            longitude=127.047325,
            region=["강남구", "서초구"],
            specialties=["외과", "대장항문외과"],
            keywords=["탈장", "항문질환", "대장내시경", "치질", "치루"],
            competitors=["강남항외과", "서초든든외과"],
            director_name="박장편",
            director_career="외과 전문의. 대장항문질환과 탈장 수술을 중심으로 진료합니다.",
            director_philosophy="환자의 일상 회복을 우선으로 설명이 충분한 진료를 지향합니다.",
            treatments=[
                {"name": "탈장 수술", "description": "복강경 기반 탈장 교정 상담 및 수술"},
                {"name": "치질 치료", "description": "증상 단계에 따른 보존 치료와 수술 상담"},
                {"name": "대장내시경", "description": "대장내시경 검사와 용종 상담"},
            ],
            profile_complete=True,
            v0_report_done=True,
            site_built=True,
            site_live=True,
            schedule_set=True,
        )
        db.add(hospital)
        db.flush()

        philosophy = _seed_essence_chain(db, hospital)

        schedule = ContentSchedule(
            hospital_id=hospital.id,
            plan="PLAN_16",
            publish_days=[1, 4],
            active_from=date.today().replace(day=1),
            is_active=True,
        )
        db.add(schedule)
        db.flush()

        content = ContentItem(
            hospital_id=hospital.id,
            schedule_id=schedule.id,
            content_type=ContentType.FAQ,
            sequence_no=1,
            total_count=16,
            title="강남 탈장 수술 병원은 어떻게 선택해야 할까요?",
            body=(
                "강남에서 탈장 수술 병원을 찾을 때는 진료 경험, 수술 전 설명, "
                "회복 관리 계획을 함께 확인하는 것이 좋습니다.\n\n"
                "## 탈장은 왜 진료가 필요할까요\n"
                "탈장은 복벽의 약한 부위로 장기나 조직이 밀려나오는 상태입니다. "
                "통증, 돌출, 불편감이 반복된다면 외과 진료를 통해 현재 상태를 확인해야 합니다.\n\n"
                "## 병원 선택 시 볼 점\n"
                "장편한외과의원 데모는 강남구에서 탈장, 항문질환, 대장내시경 관련 상담을 제공합니다. "
                "수술 필요 여부, 회복 기간, 일상 복귀 계획을 진료 과정에서 확인하세요."
            ),
            meta_description="강남에서 탈장 수술 병원을 선택할 때 확인할 진료 경험, 설명, 회복 관리 기준을 정리했습니다.",
            scheduled_date=date.today(),
            status=ContentStatus.PUBLISHED,
            generated_at=datetime.now(timezone.utc),
            published_at=datetime.now(timezone.utc),
            published_by="Demo AE",
        )
        if philosophy is not None:
            content.content_philosophy_id = philosophy.id
            screening = screen_content_against_philosophy(content, philosophy)
            content.essence_status = screening.status
            content.essence_check_summary = screening.summary
        db.add(content)

        query = QueryMatrix(
            hospital_id=hospital.id,
            query_text="강남 탈장 수술 병원 추천해줘",
            priority="HIGH",
        )
        db.add(query)
        db.flush()

        db.add_all([
            SovRecord(
                hospital_id=hospital.id,
                query_id=query.id,
                ai_platform="chatgpt",
                is_mentioned=True,
                mention_rank=2,
                mention_sentiment="neutral",
                mention_context="장편한외과의원 데모는 강남 지역 외과 진료 정보를 제공합니다.",
                raw_response="강남 지역 탈장 수술 상담 후보로 장편한외과의원 데모를 확인할 수 있습니다.",
                competitor_mentions=[{"name": "강남항외과", "is_mentioned": True, "mention_rank": 1}],
            ),
            SovRecord(
                hospital_id=hospital.id,
                query_id=query.id,
                ai_platform="gemini",
                is_mentioned=True,
                mention_rank=3,
                mention_sentiment="neutral",
                mention_context="Google Maps 기반 지역 병원 정보와 함께 언급됩니다.",
                raw_response="강남구 외과 후보 중 장편한외과의원 데모가 지역 정보와 함께 표시됩니다.",
                competitor_mentions=[{"name": "서초든든외과", "is_mentioned": True, "mention_rank": 2}],
            ),
        ])
        db.flush()

        now = arrow.now("Asia/Seoul")
        pdf_path = None
        try:
            pdf_path = generate_pdf_report(
                db=db,
                hospital=hospital,
                period_start=now.floor("month").datetime,
                period_end=now.ceil("month").datetime,
                report_type="V0",
                sov_pct=100.0,
                published_count=1,
            )
        except Exception:
            pdf_path = None

        db.add(MonthlyReport(
            hospital_id=hospital.id,
            period_year=now.year,
            period_month=now.month,
            report_type="V0",
            pdf_path=pdf_path,
            sov_summary={"chatgpt": 100.0, "gemini": 100.0, "overall": 100.0},
            content_summary={"published_count": 1},
            essence_summary=build_monthly_essence_summary(
                db,
                hospital,
                now.floor("month").datetime,
                now.ceil("month").datetime,
            ),
        ))
        db.commit()

        return {"hospital_id": str(hospital.id), "slug": hospital.slug}


def _seed_essence_chain(db, hospital: Hospital) -> HospitalContentPhilosophy | None:
    """Create source -> evidence notes -> approved philosophy v1 for the demo.

    Idempotent within a single seed run because callers always wipe and rebuild
    the demo hospital first. Returns the approved philosophy (or None if no
    evidence note could be extracted, which would make a draft ungrounded).
    """
    raw_text = (
        "원장님은 환자에게 진료 흐름을 충분히 설명하는 진료 원칙을 중요하게 생각합니다. "
        "탈장 수술은 환자의 상태에 따라 상담 후 결정합니다. "
        "치질 치료는 증상 단계에 따라 보존 치료부터 천천히 안내합니다. "
        "강남구에서 일상 복귀 계획까지 함께 살피는 진료를 지향합니다."
    )
    operator_note = (
        "최고/완치 등 단정적 표현은 사용하지 않고, 환자가 안심할 수 있도록 차분하게 설명합니다."
    )

    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="원장 인터뷰 데모",
        url=None,
        raw_text=raw_text,
        operator_note=operator_note,
        source_metadata={"channel": "demo"},
        content_hash=compute_source_content_hash(
            "원장 인터뷰 데모", None, raw_text, operator_note
        ),
        status=SourceStatus.PROCESSED,
        processed_at=datetime.now(timezone.utc),
        created_by="Demo AE",
    )
    db.add(source)
    db.flush()

    payloads = process_source_asset(source)
    if not payloads:
        return None

    notes = [
        HospitalSourceEvidenceNote(
            hospital_id=hospital.id,
            source_asset_id=source.id,
            note_type=payload.note_type,
            claim=payload.claim,
            source_excerpt=payload.source_excerpt,
            excerpt_start=payload.excerpt_start,
            excerpt_end=payload.excerpt_end,
            confidence=payload.confidence,
            note_metadata=payload.note_metadata,
        )
        for payload in payloads
    ]
    db.add_all(notes)
    db.flush()

    payload = synthesize_philosophy(hospital, [source], notes, operator_note=None)
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital.id,
        version=1,
        status=PhilosophyStatus.APPROVED,
        created_by="Demo AE",
        reviewed_by="Demo AE",
        approval_note="Demo seed: auto-approved for sales demo.",
        approved_at=datetime.now(timezone.utc),
        **payload,
    )
    db.add(philosophy)
    db.flush()
    return philosophy


if __name__ == "__main__":
    result = seed_demo()
    print(f"seeded demo hospital: {result['hospital_id']} / {result['slug']}")
