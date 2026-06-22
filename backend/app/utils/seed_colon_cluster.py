"""One-off: 대장내시경/대장용종/대장암 deep-format 콘텐츠 클러스터 seed (장편한외과).

AEO 감사 #1 권고 — 병원 최대 권위 영역(대장내시경, 블로그 315편)에 deep-format
(DISEASE/TREATMENT/COLUMN) 콘텐츠가 0이라 보강한다. 제품의 실제 생성 파이프라인
(_generate_single_content_item: generate_content + 의료광고법 forbidden screen +
essence 정렬 screen + Imagen)을 그대로 사용 — 승인된 philosophy에 ALIGNED인 것만 발행.

멱등/재시도: content_brief.seed_item(타겟별 키)로 이미 PUBLISHED면 skip, 실패해서
DRAFT로 남은 seed_tag 항목은 시작 시 정리하고 재생성한다.
실행(prod): backend 이미지로 Cloud Run Job SERVICE=seed-colon-cluster.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentSchedule, ContentStatus, ContentType
from app.models.hospital import Hospital
from app.services.content_brief import BRIEF_STATUS_APPROVED
from app.workers.tasks import _generate_single_content_item

logger = logging.getLogger(__name__)

SEED_TAG = "colon-cluster-v1"
HOSPITAL_SLUG = "jangpyeonhanoegwayiweon"

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
            "seed_item": "colon-polyp",
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
            "seed_item": "colon-scope",
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
            "seed_item": "colon-cancer-screening",
        },
    },
    {
        "content_type": ContentType.COLUMN,
        "brief": {
            "target_query": "대장내시경 정기검진 중요성 조기발견",
            "patient_intent": "대장내시경 정기검진이 왜 중요한지, 일반적으로 어떤 가치가 있는지 알고 싶다",
            "treatment_narrative": "대장내시경 정기검진과 대장암 조기발견의 일반적 의학적 가치를 진료 현장 관점에서 차분히 전달",
            # essence avoid_message 충돌 방지 — 칼럼이 홍보/비교우위/단정 톤으로 흘러
            # avoid 문구를 substring으로 포함하면 NEEDS_REVIEW로 막힌다. 정보 제공 톤만.
            "avoid_messages": [
                "최고", "1등", "유일", "가장", "완치", "보장", "확실히", "무조건",
                "타 병원보다", "다른 병원과 달리", "추천합니다", "내원하세요",
            ],
            "operator_notes": [
                "원장 칼럼 — 이성근 원장명+전문성 co-occurrence, 차분한 1인칭 진료 관점",
                "국립암센터 대장암센터 전임의 배경은 사실로만 1회 언급",
                "절대 홍보·비교우위·단정·권유 표현 금지. 특정 병원 우월성 주장 금지.",
                "일반적 의학 사실과 정기검진의 보편적 가치만, 정보 제공 톤(광고 아님).",
            ],
            "seed_tag": SEED_TAG,
            "seed_item": "colon-column",
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

        # 정리: (a) 실패해 DRAFT로 남은 seed_tag 항목, (b) seed_item 키가 없는 1차 run
        # 레거시(현재 per-target 멱등 키와 안 맞아 중복 발행된 것) — 둘 다 제거.
        stale = (
            db.execute(
                select(ContentItem).where(
                    ContentItem.hospital_id == hospital.id,
                    ContentItem.content_brief.op("->>")("seed_tag") == SEED_TAG,
                    or_(
                        ContentItem.status != ContentStatus.PUBLISHED,
                        ContentItem.content_brief.op("->>")("seed_item").is_(None),
                    ),
                )
            )
            .scalars()
            .all()
        )
        for d in stale:
            logger.info("cleanup stale/legacy seed item: %s (%s) status=%s", d.id, d.content_type, d.status)
            db.delete(d)
        if stale:
            db.commit()

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
            seed_item = target["brief"]["seed_item"]
            # 이미 발행된 타겟이면 skip (멱등).
            done = db.execute(
                select(ContentItem).where(
                    ContentItem.hospital_id == hospital.id,
                    ContentItem.content_brief.op("->>")("seed_item") == seed_item,
                    ContentItem.status == ContentStatus.PUBLISHED,
                )
            ).scalars().first()
            if done:
                logger.info("skip %s — already published.", seed_item)
                continue

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
                _generate_single_content_item(db, item, hospital)
            except Exception as e:  # noqa: BLE001
                logger.error("GEN FAIL %s (%s): %s", seed_item, target["content_type"], e)
                db.rollback()
                continue
            db.refresh(item)
            logger.info(
                "generated %s essence=%s title=%s", seed_item, item.essence_status, (item.title or "")[:48]
            )
            # DRAFT 전용 — essence만으로 발행하지 않는다. essence는 날조 통계·과장/최상급·
            # 의학 오류를 못 잡으므로(하네스+codex 감사에서 입증), 생성물은 DRAFT로 두고
            # 하네스+codex 감사 통과 후 사람(AE)이 발행한다. (auto-publish 금지)
            if item.body:
                published += 1
            db.commit()
            logger.info(
                "DRAFT %s — essence=%s body=%s (auto-publish 금지: 감사 후 수동 발행)",
                seed_item,
                item.essence_status,
                bool(item.body),
            )

        logger.info("Colon cluster seed complete: %d generated as DRAFT (none auto-published).", published)


if __name__ == "__main__":
    main()
