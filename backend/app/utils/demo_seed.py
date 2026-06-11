"""Sales-ready fictional orthopedic demo seed.

The seed builds the same inputs the production workflow uses: hospital profile,
source-backed Essence notes, approved writing standard, AI query targets,
content slots, and approved content briefs. Use ``--generate --publish`` to run
the existing generation pipeline over those slots and publish aligned drafts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import arrow
from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentSchedule, ContentStatus
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
from app.models.sov import AIQueryTarget, AIQueryVariant, QueryMatrix, SovRecord
from app.services.asset_storage import store_asset_bytes
from app.services.content_brief import BRIEF_STATUS_APPROVED, build_content_brief
from app.services.content_calendar import generate_monthly_slots
from app.services.essence_engine import (
    ESSENCE_STATUS_ALIGNED,
    build_monthly_essence_summary,
    compute_source_content_hash,
    process_source_asset,
    synthesize_philosophy,
)
from app.services.report_engine import generate_pdf_report

DEMO_SLUG = "motionlabs-orthopedics-demo"
DEMO_NAME = "모션랩스정형외과의원"
DEMO_CONTENT_COUNT = 16
DEMO_MIN_GENERATED_COUNT = 10
_ASSET_DIR = Path(__file__).resolve().parents[1] / "demo_assets"


def seed_demo(*, generate: bool = False, publish: bool = False) -> dict[str, str | int]:
    """Reset and seed the fictional orthopedic sales demo."""
    with SyncSessionLocal() as db:
        existing = db.execute(select(Hospital).where(Hospital.slug == DEMO_SLUG)).scalar_one_or_none()
        if existing:
            db.delete(existing)
            db.commit()

        hospital = _create_hospital()
        db.add(hospital)
        db.flush()

        philosophy = _seed_essence_chain(db, hospital)
        public_assets = _seed_demo_photos(db, hospital)
        content_image_url = _content_image_url(hospital.slug, public_assets)
        query_targets = _seed_query_targets(db, hospital)
        content_items = _seed_content_slots(db, hospital, philosophy, query_targets, content_image_url)
        _seed_sov_snapshot(db, hospital, query_targets)
        _seed_report(db, hospital)
        db.commit()

        generated_count = 0
        published_count = 0
        if generate:
            generated_count, published_count = _run_generation_pipeline(
                db,
                hospital_id=hospital.id,
                publish=publish,
                fallback_image_url=content_image_url,
            )

        return {
            "hospital_id": str(hospital.id),
            "slug": hospital.slug,
            "slots": len(content_items),
            "generated": generated_count,
            "published": published_count,
        }


def _create_hospital() -> Hospital:
    return Hospital(
        name=DEMO_NAME,
        slug=DEMO_SLUG,
        status=HospitalStatus.ACTIVE,
        plan=Plan.PLAN_16,
        address="서울시 성동구 아차산로 38 개풍빌딩 4층",
        phone="02-6203-3811",
        business_hours={
            "mon": "09:00-19:00",
            "tue": "09:00-19:00",
            "wed": "09:00-20:30",
            "thu": "09:00-19:00",
            "fri": "09:00-19:00",
            "sat": "09:00-14:00",
            "sun": "휴진",
        },
        website_url="https://motionlabs-orthopedics.example.com",
        blog_url="https://blog.naver.com/motionlabs-ortho-demo",
        kakao_channel_url="https://pf.kakao.com/_motionlabs_demo",
        google_business_profile_url="https://business.google.com/example/motionlabs-orthopedics",
        google_maps_url="https://maps.google.com/?q=서울시+성동구+아차산로+38",
        naver_place_url="https://naver.me/motionlabs-demo",
        aeo_domain="motionlabs-orthopedics-demo.reputation.co.kr",
        latitude=37.544638,
        longitude=127.055914,
        region=["서울", "성동구", "성수동"],
        specialties=["정형외과", "스포츠손상", "재활의학 협진"],
        keywords=["무릎 통증", "어깨 통증", "허리 통증", "스포츠 손상", "도수재활", "관절 주사"],
        competitors=["성수바른정형외과", "서울숲튼튼정형외과", "건대입구재활의학과"],
        director_name="김모션",
        director_career=(
            "정형외과 전문의. 어깨·무릎·척추 통증, 스포츠 손상, 비수술 치료와 "
            "운동 재활 계획을 중심으로 진료합니다."
        ),
        director_philosophy=(
            "영상 검사 결과만 보지 않고 통증이 생긴 생활 맥락, 직업, 운동 습관을 함께 확인해 "
            "환자가 납득할 수 있는 단계별 치료 계획을 세웁니다."
        ),
        director_credentials={
            "board_certifications": ["정형외과 전문의"],
            "society_memberships": ["대한정형외과학회", "대한스포츠의학회"],
        },
        treatments=[
            {
                "name": "무릎 통증 진료",
                "description": "퇴행성 변화, 반월상연골 손상, 러닝 후 통증을 구분해 단계별 치료를 안내합니다.",
            },
            {
                "name": "어깨 통증 진료",
                "description": "회전근개 질환, 오십견, 충돌증후군 가능성을 진찰과 영상으로 확인합니다.",
            },
            {
                "name": "허리·목 통증 진료",
                "description": "디스크성 통증, 자세성 통증, 신경 증상을 나누어 생활 관리와 치료 방향을 정리합니다.",
            },
            {
                "name": "스포츠 손상",
                "description": "운동 복귀 시점과 재손상 예방을 고려해 치료와 재활 계획을 세웁니다.",
            },
            {
                "name": "도수재활",
                "description": "진단 후 필요한 범위에서 관절 가동성, 근력, 움직임 패턴 회복을 돕습니다.",
            },
            {
                "name": "관절 주사 상담",
                "description": "주사 치료가 필요한 상황과 기대 범위, 이후 관리 계획을 함께 설명합니다.",
            },
        ],
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=True,
    )


def _seed_essence_chain(db, hospital: Hospital) -> HospitalContentPhilosophy | None:
    """Create source -> evidence notes -> approved philosophy v1 for the demo."""
    raw_text = (
        "모션랩스정형외과의원은 서울 성동구 성수동에서 직장인, 러너, 생활 스포츠 환자가 "
        "자주 겪는 무릎 통증, 어깨 통증, 허리 통증, 스포츠 손상을 진료하는 가상 정형외과입니다. "
        "김모션 대표원장은 정형외과 전문의로, 첫 진료에서 통증 시작 시점, 악화 동작, 운동 습관, "
        "업무 자세를 함께 확인합니다. 영상 검사 결과만으로 치료를 정하지 않고 진찰 소견과 환자의 "
        "생활 목표를 함께 설명합니다. 치료는 운동 조절과 약물, 물리치료, 도수재활, 주사 상담을 "
        "단계적으로 검토하며, 수술 가능성이 의심되는 경우에는 필요한 기준과 의뢰 방향을 분명히 안내합니다. "
        "콘텐츠는 환자가 AI 검색에서 '성수동 무릎 통증', '어깨가 안 올라갈 때', '러닝 후 무릎 통증'처럼 "
        "묻는 상황에 답하도록 작성합니다."
    )
    operator_note = (
        "완치, 최고, 1등, 통증 없는 치료처럼 보장·비교 표현은 쓰지 않는다. "
        "비수술을 무조건 강조하지 말고, 진단 후 단계적으로 결정한다는 톤을 유지한다. "
        "성수동 직장인과 생활 스포츠 환자 맥락을 자연스럽게 반영한다."
    )

    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.INTERVIEW,
        title="김모션 대표원장 온보딩 인터뷰",
        url=None,
        raw_text=raw_text,
        operator_note=operator_note,
        source_metadata={"channel": "sales_demo", "specialty": "orthopedics"},
        content_hash=compute_source_content_hash(
            "김모션 대표원장 온보딩 인터뷰", None, raw_text, operator_note
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
        approval_note="Sales demo: source-backed orthopedic writing standard.",
        approved_at=datetime.now(timezone.utc),
        **payload,
    )
    db.add(philosophy)
    db.flush()
    return philosophy


def _seed_demo_photos(db, hospital: Hospital) -> list[HospitalSourceAsset]:
    specs = [
        {
            "source_type": SourceType.PHOTO_CLINIC_INTERIOR,
            "title": "모션랩스정형외과의원 접수·대기 공간",
            "filename": "motionlabs-orthopedics-hero.png",
            "asset_filename": "motionlabs-orthopedics-hero.png",
        },
        {
            "source_type": SourceType.PHOTO_DOCTOR,
            "title": "김모션 대표원장",
            "filename": "motionlabs-orthopedics-doctor.png",
            "asset_filename": "motionlabs-orthopedics-doctor.png",
        },
        {
            "source_type": SourceType.PHOTO_TREATMENT_ROOM,
            "title": "정형외과 상담·재활 안내 공간",
            "filename": "motionlabs-orthopedics-content.png",
            "asset_filename": "motionlabs-orthopedics-content.png",
        },
    ]

    assets: list[HospitalSourceAsset] = []
    for spec in specs:
        asset_path = _ASSET_DIR / spec["asset_filename"]
        image_bytes = asset_path.read_bytes()
        file_url = store_asset_bytes(
            hospital_id=hospital.id,
            filename=spec["filename"],
            data=image_bytes,
            mime_type="image/png",
        )
        asset = HospitalSourceAsset(
            hospital_id=hospital.id,
            source_type=spec["source_type"],
            title=spec["title"],
            url=None,
            raw_text=None,
            operator_note="GPT image 2 generated fictional demo asset; no real clinic/person likeness.",
            source_metadata={"channel": "gpt_image_2_demo", "fictional": True},
            file_url=file_url,
            mime_type="image/png",
            file_size_bytes=len(image_bytes),
            is_public=True,
            content_hash=compute_source_content_hash(spec["title"], None, None, None),
            status=SourceStatus.PROCESSED,
            processed_at=datetime.now(timezone.utc),
            created_by="Demo AE",
        )
        db.add(asset)
        assets.append(asset)
    db.flush()
    return assets


def _content_image_url(slug: str, assets: list[HospitalSourceAsset]) -> str | None:
    for asset in assets:
        if asset.source_type == SourceType.PHOTO_TREATMENT_ROOM:
            return f"/api/v1/public/hospitals/{slug}/assets/{asset.id}"
    return None


def _seed_query_targets(db, hospital: Hospital) -> list[AIQueryTarget]:
    now_month = arrow.now("Asia/Seoul").format("YYYY-MM")
    specs = [
        ("성수동 무릎 통증", "증상 탐색", "무릎 통증", "무릎 통증 진료", ["통증 위치", "운동 복귀", "검사 필요성"]),
        ("러닝 후 무릎 통증", "운동 손상", "러닝 후 무릎 통증", "스포츠 손상", ["휴식 기준", "재활", "재손상 예방"]),
        ("어깨가 안 올라갈 때", "증상 탐색", "어깨 통증", "어깨 통증 진료", ["오십견", "회전근개", "진료 시점"]),
        ("성수동 어깨 통증 정형외과", "지역 탐색", "어깨 통증", "어깨 통증 진료", ["접근성", "검사", "치료 선택"]),
        ("허리 통증 다리 저림", "증상 탐색", "허리 통증", "허리·목 통증 진료", ["신경 증상", "MRI 기준", "생활 관리"]),
        ("목 통증 팔 저림", "증상 탐색", "목 통증", "허리·목 통증 진료", ["신경 증상", "자세", "진료 시점"]),
        ("스포츠 손상 운동 복귀", "치료 결정", "스포츠 손상", "스포츠 손상", ["복귀 기준", "재활 단계", "재손상 예방"]),
        ("도수재활 언제 필요할까", "치료 결정", "근골격계 통증", "도수재활", ["적응증", "횟수", "자가운동"]),
        ("관절 주사 맞아도 될까", "치료 결정", "관절 통증", "관절 주사 상담", ["기대 범위", "주의사항", "대안"]),
        ("성수동 직장인 손목 통증", "지역 탐색", "손목 통증", "정형외과 진료", ["업무 자세", "보조기", "검사"]),
        ("계단 내려갈 때 무릎 통증", "증상 탐색", "무릎 통증", "무릎 통증 진료", ["연골", "근력", "진료 시점"]),
        ("오십견과 회전근개 차이", "질환 비교", "어깨 통증", "어깨 통증 진료", ["감별", "영상검사", "운동 범위"]),
    ]

    targets: list[AIQueryTarget] = []
    for name, intent, symptom, treatment, criteria in specs:
        query = QueryMatrix(hospital_id=hospital.id, query_text=name, priority="HIGH")
        db.add(query)
        db.flush()
        target = AIQueryTarget(
            hospital_id=hospital.id,
            name=name,
            target_intent=intent,
            region_terms=["성동구", "성수동"],
            specialty="정형외과",
            condition_or_symptom=symptom,
            treatment=treatment,
            decision_criteria=criteria,
            platforms=["CHATGPT", "GEMINI", "PERPLEXITY"],
            competitor_names=hospital.competitors,
            priority="HIGH",
            status="ACTIVE",
            target_month=now_month,
            created_by="Demo AE",
            updated_by="Demo AE",
        )
        db.add(target)
        db.flush()
        db.add(
            AIQueryVariant(
                query_target_id=target.id,
                query_text=f"{name} 병원 선택 기준을 알려줘",
                platform="CHATGPT",
                language="ko",
                is_active=True,
                query_matrix_id=query.id,
            )
        )
        targets.append(target)
    db.flush()
    return targets


def _seed_content_slots(
    db,
    hospital: Hospital,
    philosophy: HospitalContentPhilosophy | None,
    query_targets: list[AIQueryTarget],
    content_image_url: str | None,
) -> list[ContentItem]:
    target_month = arrow.now("Asia/Seoul").shift(months=1).floor("month")
    slots = generate_monthly_slots(
        plan=Plan.PLAN_16.value,
        publish_days=[0, 1, 2, 3, 4],
        target_month=target_month,
    )[:DEMO_CONTENT_COUNT]
    schedule = ContentSchedule(
        hospital_id=hospital.id,
        plan=Plan.PLAN_16.value,
        publish_days=[0, 1, 2, 3, 4],
        active_from=target_month.date(),
        is_active=True,
    )
    db.add(schedule)
    db.flush()

    items: list[ContentItem] = []
    for index, (scheduled_date, content_type, sequence_no, total_count) in enumerate(slots):
        query_target = query_targets[index % len(query_targets)]
        item = ContentItem(
            hospital_id=hospital.id,
            schedule_id=schedule.id,
            content_type=content_type,
            sequence_no=sequence_no,
            total_count=total_count,
            scheduled_date=scheduled_date,
            status=ContentStatus.DRAFT,
            query_target_id=query_target.id,
            image_url=content_image_url,
            image_prompt="GPT image 2 orthopedic clinic educational thumbnail for sales demo.",
        )
        db.add(item)
        db.flush()
        item.content_brief = build_content_brief(
            hospital=hospital,
            content_item=item,
            query_target=query_target,
            philosophy=philosophy,
        )
        item.brief_status = BRIEF_STATUS_APPROVED
        item.brief_approved_at = datetime.now(timezone.utc)
        item.brief_approved_by = "Demo AE"
        items.append(item)
    db.flush()
    return items


def _seed_sov_snapshot(db, hospital: Hospital, query_targets: list[AIQueryTarget]) -> None:
    matrix_rows = db.execute(
        select(QueryMatrix).where(QueryMatrix.hospital_id == hospital.id).limit(5)
    ).scalars().all()
    for index, query in enumerate(matrix_rows):
        db.add(
            SovRecord(
                hospital_id=hospital.id,
                query_id=query.id,
                ai_query_target_id=query_targets[index].id if index < len(query_targets) else None,
                ai_platform="chatgpt",
                is_mentioned=index < 3,
                mention_rank=index + 2 if index < 3 else None,
                mention_sentiment="neutral",
                mention_context=(
                    "성수동 정형외과 선택지 중 모션랩스정형외과의원이 진료 정보 허브와 함께 언급됩니다."
                    if index < 3
                    else "경쟁 병원 중심으로 답변되어 보완 콘텐츠가 필요한 상태입니다."
                ),
                raw_response=(
                    "성수동에서 무릎·어깨 통증을 상담할 정형외과 후보로 "
                    "모션랩스정형외과의원의 진료 정보 허브를 확인할 수 있습니다."
                ),
                competitor_mentions=[{"name": "성수바른정형외과", "is_mentioned": True, "mention_rank": 1}],
            )
        )
    db.flush()


def _seed_report(db, hospital: Hospital) -> None:
    now = arrow.now("Asia/Seoul")
    pdf_path = None
    try:
        pdf_path = generate_pdf_report(
            hospital=hospital,
            period_start=now.floor("month").datetime,
            period_end=now.ceil("month").datetime,
            report_type="V0",
            sov_pct=60.0,
            published_count=0,
        )
    except Exception:
        pdf_path = None

    db.add(
        MonthlyReport(
            hospital_id=hospital.id,
            period_year=now.year,
            period_month=now.month,
            report_type="V0",
            pdf_path=pdf_path,
            sov_summary={"chatgpt": 60.0, "gemini": 40.0, "overall": 50.0},
            content_summary={"published_count": 0, "planned_count": DEMO_CONTENT_COUNT},
            essence_summary=build_monthly_essence_summary(
                db,
                hospital,
                now.floor("month").datetime,
                now.ceil("month").datetime,
            ),
        )
    )


def _run_generation_pipeline(
    db,
    *,
    hospital_id,
    publish: bool,
    fallback_image_url: str | None,
) -> tuple[int, int]:
    """Run the existing single-item generation pipeline over seeded slots."""
    from app.workers.tasks import _generate_single_content_item

    hospital = db.get(Hospital, hospital_id)
    if not hospital:
        return 0, 0

    items = db.execute(
        select(ContentItem)
        .where(ContentItem.hospital_id == hospital.id)
        .order_by(ContentItem.sequence_no)
        .limit(DEMO_CONTENT_COUNT)
    ).scalars().all()

    generated_count = 0
    published_count = 0
    failures: list[str] = []
    for item in items:
        try:
            _generate_single_content_item(db, item, hospital)
        except Exception as exc:
            failures.append(f"#{item.sequence_no}: {exc}")
            continue
        db.refresh(item)
        if not item.image_url and fallback_image_url:
            item.image_url = fallback_image_url
            item.image_prompt = "GPT image 2 orthopedic clinic educational thumbnail for sales demo."
            db.commit()

        if item.body:
            generated_count += 1

        if publish and item.body and item.essence_status == ESSENCE_STATUS_ALIGNED:
            item.status = ContentStatus.PUBLISHED
            item.published_at = datetime.now(timezone.utc) - timedelta(minutes=item.sequence_no)
            item.published_by = "Demo AE"
            item.body_updated_at = item.body_updated_at or datetime.now(timezone.utc)
            published_count += 1
            db.commit()

    _refresh_report_summary(db, hospital)
    if generated_count < DEMO_MIN_GENERATED_COUNT:
        failure_summary = "; ".join(failures[:3]) if failures else "no generated bodies"
        raise RuntimeError(
            "Demo generation did not produce enough pipeline content "
            f"({generated_count}/{DEMO_MIN_GENERATED_COUNT} generated). {failure_summary}"
        )
    if publish and published_count < DEMO_MIN_GENERATED_COUNT:
        raise RuntimeError(
            "Demo publishing did not expose enough aligned content "
            f"({published_count}/{DEMO_MIN_GENERATED_COUNT} published)."
        )
    return generated_count, published_count


def _refresh_report_summary(db, hospital: Hospital) -> None:
    published_items = db.execute(
        select(ContentItem).where(
            ContentItem.hospital_id == hospital.id,
            ContentItem.status == ContentStatus.PUBLISHED,
        )
    ).scalars().all()
    report = db.execute(
        select(MonthlyReport).where(MonthlyReport.hospital_id == hospital.id)
    ).scalar_one_or_none()
    if report:
        now = arrow.now("Asia/Seoul")
        report.content_summary = {
            "published_count": len(published_items),
            "planned_count": DEMO_CONTENT_COUNT,
        }
        report.essence_summary = build_monthly_essence_summary(
            db,
            hospital,
            now.floor("month").datetime,
            now.ceil("month").datetime,
        )
        db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the MotionLabs Orthopedics sales demo.")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Run the actual content generation pipeline for the seeded slots.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish generated content that passes the approved Essence screening.",
    )
    args = parser.parse_args()

    result = seed_demo(generate=args.generate, publish=args.publish)
    print(
        "seeded demo hospital: "
        f"{result['hospital_id']} / {result['slug']} "
        f"(slots={result['slots']}, generated={result['generated']}, published={result['published']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
