"""One-off: 원장 프로파일에서 비존재 자격 '대장내시경 세부전문의' 제거 (의료법 §56 거짓 자격).

codex 독립 감사 확인: 한국에 '대장내시경 세부전문의' 보드 세부전문의는 없다. 실재 명칭은
'소화기내시경 세부전문의'(대한소화기내시경학회, 내과/외과/소아청소년과 전문의 대상). 원장이 실제
보유 확인되면 AE가 정확 명칭으로 재입력한다. 여기서는 검증 불가한 잘못된 표기만 제거한다.

director_career(약력 문자열) + director_credentials.board_certifications(JSON)에서 모두 제거.
멱등. 실행: backend 이미지로 Cloud Run Job SERVICE=fix-director-credential.
"""
import logging

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.models.hospital import Hospital

logger = logging.getLogger(__name__)

HOSPITAL_SLUG = "jangpyeonhanoegwayiweon"
BAD = "대장내시경 세부전문의"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SyncSessionLocal() as db:
        h = db.execute(
            select(Hospital).where(Hospital.slug == HOSPITAL_SLUG)
        ).scalar_one_or_none()
        if not h:
            logger.error("hospital not found: %s", HOSPITAL_SLUG)
            return

        if h.director_career and BAD in h.director_career:
            h.director_career = (
                h.director_career.replace(BAD + ", ", "")
                .replace(", " + BAD, "")
                .replace(BAD, "")
            )
            logger.info("director_career cleaned")

        cred = h.director_credentials
        if isinstance(cred, dict):
            bc = cred.get("board_certifications")
            if isinstance(bc, list) and BAD in bc:
                # JSON 변경 감지를 위해 dict를 새로 만들어 재할당.
                h.director_credentials = {**cred, "board_certifications": [x for x in bc if x != BAD]}
                logger.info("board_certifications cleaned: %s", h.director_credentials["board_certifications"])

        db.commit()
        logger.info("done. director_career now: %s", (h.director_career or "")[:140])


if __name__ == "__main__":
    main()
