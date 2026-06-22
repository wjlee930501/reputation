"""One-off: 대장내시경/대장용종/대장암 deep-format 콘텐츠 클러스터 seed (장편한외과).

AEO 감사 #1 권고 — 병원 최대 권위 영역(대장내시경, 블로그 315편)에 deep-format
(DISEASE/TREATMENT/COLUMN) 콘텐츠가 0이라 보강한다. 제품의 실제 생성 파이프라인
(_generate_single_content_item: generate_content + 의료광고법 forbidden screen +
essence 정렬 screen + Imagen)을 그대로 사용 — 승인된 philosophy에 ALIGNED인 것만 발행,
나머지는 DRAFT로 남긴다(안전).

멱등: content_brief.seed_tag='colon-cluster-v1' 마커로 재실행 시 중복 생성 방지.
실행(prod): backend 이미지로 Cloud Run Job SERVICE=seed-colon-cluster.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital
from app.services.content_brief import BRIEF_STATUS_APPROVED
from app.services.essence_engine import ESSENCE_STATUS_ALIGNED
from app.workers.tasks import _generate_single_content_item

logger = logging.getLogger(__name__)

SEED_TAG = "colon-cluster-v1"
HOSPITAL_SLUG = "jangpyeonhanoegwayiweon"

# content_brief가 생성 주제를 조향한다 (_build_content_brief_context 필드).
TARGETS = [
    {
        "content_type": ContentType.DISEASE,
        "brief": {
            "target_query": "대장용종 증상 원인 제거",
            "patient_intent": "대장용종이 무엇이고 왜 생기는지, 암으로 진행하는지, 꼭 제거해야 하는지 알고 싶다",
            "treatment_narrative": "대장용종의 종류(선종성/과형성 등)와 대장암 진행 위험, 대장내시경 중 용종절제의 의미를 환자 언어로 설명",
            "operator_notes": [
                "deep-format 질환 가이드 — 원인/종류/증상/진단/관리까지",
                "이성근 원장 국립암센터 대장암센터 배경을 신뢰축으로 자연스럽게",
                "국가암정보센터·대한대장항문학회·질병관리청 등 공개 가이드 인용",
            ],
            "seed_tag": SEED_TAG,
        },
    },
    {
        "content_type": ContentType.TREATMENT,
        "brief": {
            "target_query": "수원 대장내시경 검사 과정 준비 수면",
            "patient_intent": "대장내시경을 어떻게 받는지, 장정결(장청소) 준비와 수면 여부, 검사 후 회복이 궁금하다",
            "treatment_narrative": "검사 전 식이/장정결 → 당일 수면/비수면 선택 → 검사·용종절제 → 회복·주의사항을 단계로 안내",
            "operator_notes": [
                "deep-format 시술 안내 — 단계별(### 1단계 …) 구성으로 과정/회복/주의",
                "장정결 성공 팁, 수면 vs 비수면 비교",
                "공개 진료지침 인용, 개인별 판단은 진료 상담 필요 고지",
            ],
            "seed_tag": SEED_TAG,
        },
    },
    {
        "content_type": ContentType.DISEASE,
        "brief": {
            "target_query": "대장암 조기검진 시기 증상 대장내시경",
            "patient_intent": "대장암을 조기에 발견하려면 언제 어떤 검사를 받아야 하는지, 어떤 증상을 주의해야 하는지",
            "treatment_narrative": "대장암 위험요인·경고 증상(혈변/배변습관 변화 등)과 대장내시경 검진 권고 시기를 정리",
            "operator_notes": [
                "deep-format 질환 가이드 — 조기검진 중심",
                "국가암정보센터 권고 검진 연령/주기 인용",
                "이성근 원장 대장암센터 배경 자연스럽게",
            ],
            "seed_tag": SEED_TAG,
        },
    },
    {
        "content_type": ContentType.COLUMN,
        "brief": {
            "target_query": "대장내시경 언제 받아야 조기발견",
            "patient_intent": "대장내시경을 언제 처음 받아야 하는지, 왜 중요한지 원장 관점에서 듣고 싶다",
            "treatment_narrative": "원장이 진료 현장에서 강조하는 대장내시경 조기 검진의 가치와 환자에게 전하고 싶은 메시지",
            "operator_notes": [
                "원장 칼럼 — 이성근 원장명+전문성 co-occurrence, 1인칭 진료 관점",
                "국립암센터 대장암센터 전임의 배경 자연스럽게",
                "과장 없이 조기검진의 실제 가치 중심",
            ],
            "seed_tag": SEED_TAG,
        },
    },
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SyncSessionLocal() as db:
        hospital = db.execute(
            select(Hospital).where(Hospital.slug == HOSPITAL_SLUG)
        ).scalar_one_or_none()
        if hospital is None:
            logger.error("Hospital not found: %s", HOSPITAL_SLUG)
            return

        # 멱등 — 이미 seed된 클러스터가 있으면 중단.
        # content_brief는 generic JSON 매핑이라 .astext가 없다 → portable한 ->> 연산자로
        # seed_tag 텍스트를 추출해 비교 (postgres JSON/JSONB 모두 동작).
        existing = (
            db.execute(
                select(ContentItem).where(
                    ContentItem.hospital_id == hospital.id,
                    ContentItem.content_brief.op("->>")("seed_tag") == SEED_TAG,
                )
            )
            .scalars()
            .all()
        )
        if existing:
            logger.info("Cluster already seeded (%d items) — skipping.", len(existing))
            return

        schedule = (
            db.execute(
                select(ContentSchedule)
                .where(
                    ContentSchedule.hospital_id == hospital.id,
                    ContentSchedule.is_active.is_(True),
                )
                .order_by(ContentSchedule.active_from.desc())
            )
            .scalars()
            .first()
        )
        if schedule is None:
            logger.error("No active schedule for %s — cannot seed.", HOSPITAL_SLUG)
            return

        # 슬롯 충돌 방지 — 기존 max sequence_no 위 + distinct 과거 날짜.
        max_seq = (
            db.execute(
                select(ContentItem.sequence_no)
                .where(ContentItem.schedule_id == schedule.id)
                .order_by(ContentItem.sequence_no.desc())
            )
            .scalars()
            .first()
            or 0
        )
        base_seq = max(max_seq, 300) + 1
        base_date = date(2026, 6, 9)

        published = 0
        for idx, target in enumerate(TARGETS):
            item = ContentItem(
                hospital_id=hospital.id,
                schedule_id=schedule.id,
                content_type=target["content_type"],
                sequence_no=base_seq + idx,
                total_count=16,
                scheduled_date=base_date + timedelta(days=idx * 2),
                status=ContentStatus.DRAFT,
                content_brief=target["brief"],
                brief_status=BRIEF_STATUS_APPROVED,
                brief_approved_at=datetime.now(timezone.utc),
                brief_approved_by="MotionLabs (cluster seed)",
            )
            db.add(item)
            db.flush()
            try:
                # 제품 실제 파이프라인 — generate_content + forbidden + essence + Imagen.
                _generate_single_content_item(db, item, hospital)
            except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않도록
                logger.error("GEN FAIL #%d (%s): %s", idx, target["content_type"], e)
                db.rollback()
                continue
            db.refresh(item)
            logger.info(
                "generated #%d type=%s essence=%s title=%s",
                idx,
                item.content_type,
                item.essence_status,
                (item.title or "")[:48],
            )
            if item.body and item.essence_status == ESSENCE_STATUS_ALIGNED:
                item.status = ContentStatus.PUBLISHED
                item.published_at = datetime.now(timezone.utc) - timedelta(minutes=idx)
                item.published_by = "MotionLabs (cluster seed)"
                item.body_updated_at = item.body_updated_at or datetime.now(timezone.utc)
                published += 1
                db.commit()
                logger.info("PUBLISHED #%d — %s", idx, (item.title or "")[:48])
            else:
                db.commit()  # essence 미정렬/본문 없음 → DRAFT 유지 (발행 안 함).
                logger.warning(
                    "KEPT DRAFT #%d — essence=%s body=%s",
                    idx,
                    item.essence_status,
                    bool(item.body),
                )

        logger.info("Colon cluster seed complete: %d/%d published.", published, len(TARGETS))


if __name__ == "__main__":
    main()
