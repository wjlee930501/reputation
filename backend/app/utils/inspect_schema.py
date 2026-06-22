"""One-off: prod DB 스키마 점검 (읽기 전용).

유실된 마이그레이션 0030 의 실제 정의를 prod 에서 역으로 확인하기 위한 진단 도구.
ai_query_targets 의 제약 목록 + alembic_version 현재 리비전을 출력한다.

실행 (prod): backend 이미지로 Cloud Run Job SERVICE=inspect-schema.
"""
import logging

from sqlalchemy import text

from app.core.database import SyncSessionLocal

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SyncSessionLocal() as db:
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        logger.info("alembic_version = %s", version)

        rows = db.execute(
            text(
                """
                SELECT conname, contype, pg_get_constraintdef(oid) AS condef
                FROM pg_constraint
                WHERE conrelid = 'ai_query_targets'::regclass
                ORDER BY contype, conname
                """
            )
        ).fetchall()
        logger.info("ai_query_targets constraints (%d):", len(rows))
        for r in rows:
            logger.info("  [%s] %s :: %s", r.contype, r.conname, r.condef)


if __name__ == "__main__":
    main()
