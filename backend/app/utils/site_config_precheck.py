"""읽기 전용 점검: 공개 반영을 열기 전·뒤에 병원마다 섹션 설정(레이아웃 편집기) 초안·공개 판을 지금 규칙으로 검증한다.

공개 반영과 같은 검증(`validate_site_config` + `site_config_refs.load_context`)을 돌려, 지금 공개 반영하면 422가 날
초안(이제 거절되는 칸이 남은 옛 초안)과, 병원 레이아웃과 갈라진 공개 판(공개 응답이 `site_config: null`)을 보인다.
트랜잭션 첫 문장이 `SET TRANSACTION READ ONLY`이고 끝나면 롤백한다 — 아무것도 바꾸지 않는다::

    python -m app.utils.site_config_precheck

운영에서는 migrate Job으로 돌린다::

    gcloud run jobs execute reputation-migrate --wait --args=python,-m,app.utils.site_config_precheck

출력: 설정 행마다 JSON 한 줄(`slug`·`state`·`layout`·`hospital_layout`·`diverged`·`version`·`ok`·`errors`), 마지막에
`{"summary": {...}}` 한 줄.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hospital import Hospital
from app.models.site_config import SITE_CONFIG_DRAFT, SITE_CONFIG_PUBLISHED, HospitalSiteConfig
from app.services import site_config_refs
from app.services.site_config_validation import validate_site_config


async def precheck(db: AsyncSession, *, read_only: bool = True) -> list[dict[str, Any]]:
    """`read_only=False`는 이미 쓰기가 있는 테스트 트랜잭션용이다(운영 CLI는 언제나 읽기 전용)."""

    if read_only:
        if db.in_transaction():
            await db.rollback()
        await db.execute(text("SET TRANSACTION READ ONLY"))
    try:
        rows = (
            await db.execute(
                select(HospitalSiteConfig, Hospital)
                .join(Hospital, Hospital.id == HospitalSiteConfig.hospital_id)
                .where(HospitalSiteConfig.state.in_([SITE_CONFIG_DRAFT, SITE_CONFIG_PUBLISHED]))
                .order_by(Hospital.slug, HospitalSiteConfig.state)
            )
        ).all()
        out: list[dict[str, Any]] = []
        for row, hospital in rows:
            check = validate_site_config(
                row.layout, row.config or {}, await site_config_refs.load_context(db, hospital, row.config)
            )
            out.append(
                {
                    "slug": hospital.slug,
                    "state": row.state,
                    "layout": row.layout,
                    "hospital_layout": hospital.site_layout,
                    "diverged": row.state == SITE_CONFIG_PUBLISHED and row.layout != hospital.site_layout,
                    "version": row.version,
                    "ok": check.ok,
                    "errors": [{"path": e["path"], "code": e["code"]} for e in check.errors],
                }
            )
        return out
    finally:
        if read_only:
            await db.rollback()


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "drafts": sum(row["state"] == SITE_CONFIG_DRAFT for row in rows),
        "published": sum(row["state"] == SITE_CONFIG_PUBLISHED for row in rows),
        "drafts_rejected_now": sum(row["state"] == SITE_CONFIG_DRAFT and not row["ok"] for row in rows),
        "published_rejected_now": sum(row["state"] == SITE_CONFIG_PUBLISHED and not row["ok"] for row in rows),
        "published_diverged": sum(row["diverged"] for row in rows),
    }


def write_report(rows: list[dict[str, Any]], out: TextIO) -> None:
    for row in rows:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
    out.write(json.dumps({"summary": summarize(rows)}, ensure_ascii=False) + "\n")


async def _run() -> list[dict[str, Any]]:
    from app.core.database import get_async_sessionmaker

    async with get_async_sessionmaker()() as db:
        return await precheck(db)


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    write_report(asyncio.run(_run()), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
