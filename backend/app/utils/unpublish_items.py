"""One-off: 품질 하네스가 확인한 위반 콘텐츠를 라이브에서 내린다(→DRAFT).

의료광고법(날조 통계+가짜 권위)·의료법(가짜 자격/경력)·환자안전(의학오류)·
미시행 술식/마취 오표기로 confirm된 항목만 status=DRAFT로 전환해 공개에서 제거한다.
멱등(이미 DRAFT면 no-op). 실행: backend 이미지로 Cloud Run Job SERVICE=unpublish-flagged.

재발행은 재작성+하네스 통과 후 별도로 한다(여기서는 내리기만).
"""
import logging

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.content import ContentItem, ContentStatus
from app.models.hospital import Hospital

logger = logging.getLogger(__name__)

HOSPITAL_SLUG = "jangpyeonhanoegwayiweon"

# 하네스 confirmed 위반 항목 (제목 substring). clean 항목(치질 좌욕, 대장암 조기검진,
# 수원 대장내시경 검사 과정, 대장용종, 대장내시경 장 청소, 치질 수술 통증·마취·입원)은 유지.
UNPUBLISH_TITLE_MARKERS = [
    "대장내시경 정기검진",  # COLUMN(원문/재생성 둘 다): 과장·최상급·예후단정 등 codex NO-GO
    "변비 오래 지속",                       # 미제공 검사(통과시간검사/바이오피드백) 단정
    "치핵 수술 후 주의사항",                 # 의학오류(출혈 호발시기 거꾸로)
    "치핵 예방 생활습관",                    # 날조 통계 + 과장
    "겨울철 치질 예방",                      # 날조 통계(권위 귀속)
    "치핵 재발률과 재발 방지",               # 날조 통계 + 미시행 술식(PPH/고무밴드)
    "치질 수술 방법 비교",                   # 미시행 술식 + 입원일수 오표기
    "치핵 수술 방법 비교",                   # 마취 오표기(척추/전신 단정, 미추마취 누락)
    "치핵 1기 2기",                          # 미시행 술식(고무밴드/경화) + 날조 통계
    "치질 수술 후 회복",                     # 마취 오표기(전신마취 전제) + 날조 통계
    "치핵 3기 4기",                          # 가짜 경력(국립암센터 출신) + 미시행 술식 + 날조 통계
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SyncSessionLocal() as db:
        h = db.execute(
            select(Hospital).where(Hospital.slug == HOSPITAL_SLUG)
        ).scalar_one_or_none()
        if not h:
            logger.error("hospital not found: %s", HOSPITAL_SLUG)
            return
        items = (
            db.execute(
                select(ContentItem).where(
                    ContentItem.hospital_id == h.id,
                    ContentItem.status == ContentStatus.PUBLISHED,
                )
            )
            .scalars()
            .all()
        )
        n = 0
        for it in items:
            title = it.title or ""
            if any(m in title for m in UNPUBLISH_TITLE_MARKERS):
                it.status = ContentStatus.DRAFT
                n += 1
                logger.info("UNPUBLISH -> DRAFT: %s", title[:50])
        db.commit()
        logger.info("Unpublished %d flagged items; kept %d.", n, len(items) - n)


if __name__ == "__main__":
    main()
