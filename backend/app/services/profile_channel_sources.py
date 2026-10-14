"""공식 채널 주소(홈페이지·블로그)를 근거 자료 행으로 등록한다 — 사람 PATCH와 자동 입력 적용 공용.

프로필 저장과 같은 트랜잭션에서 자료 행을 만들고, 본문 수집은 커밋 뒤 워커에 넘긴다.
동작은 `PATCH /admin/hospitals/{id}/profile`에 있던 그대로다(P4-3에서 옮김).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.celery_app import celery_app
from app.models.essence import SourceType
from app.models.hospital import Hospital
from app.services.audit_log import default_actor, write_audit_log
from app.services.essence_sources import (
    create_pending_channel_source,
    find_active_source_id_by_url,
)
from app.workers.dispatch_auth import build_dispatch_headers

logger = logging.getLogger(__name__)


# 공식 채널 주소는 저장하는 순간 근거 자료로 등록된다 — '자료로 추가' 버튼은 없다(설계 §4.3).
CHANNEL_SOURCE_TYPES: dict[str, SourceType] = {
    "website_url": SourceType.HOMEPAGE,
    "blog_url": SourceType.NAVER_BLOG,
}
# 본문을 받아오기 전까지 쓰는 제목. 워커가 페이지 <title>로 바꿔 준다.
CHANNEL_SOURCE_PROVISIONAL_TITLES: dict[str, str] = {
    "website_url": "병원 홈페이지",
    "blog_url": "네이버 블로그",
}
# 지도·채널 주소는 프로파일 표기 전용이다. 본문이 없어 근거로 쓸 수 없다 —
# admin/lib/onboarding-candidate.ts의 PROFILE_ONLY_CANDIDATE_KEYS와 같은 목록이다.
PROFILE_ONLY_CHANNEL_FIELDS = frozenset(
    {
        "naver_place_url",
        "google_business_profile_url",
        "google_maps_url",
        "kakao_channel_url",
    }
)


def submitted_channel_urls(
    hospital: Hospital, submitted_fields: Iterable[str], update_data: Mapping[str, object]
) -> dict[str, tuple[str, bool]]:
    """이번 저장이 보낸 공식 채널 주소와, 그것이 바뀐 값인지.

    저장 전에 비교해야 '바뀐 채널'을 알 수 있다 — 적용 뒤에는 이전 값이 남지 않는다.
    바뀌지 않은 주소도 함께 돌려준다: 지난번 등록이 실패해 자료 행이 없다면 같은 값을
    다시 저장하는 것만으로 스스로 회복되어야 한다.
    """
    submitted: dict[str, tuple[str, bool]] = {}
    # 지도·플레이스 주소는 이 화면에도 있지만 근거가 아니다. 목록을 명시적으로 건너뛰어야
    # 새 채널 칸이 생겼을 때 "왜 등록되지 않는지"가 코드에 남는다.
    for field in (*CHANNEL_SOURCE_TYPES, *sorted(PROFILE_ONLY_CHANNEL_FIELDS)):
        if field in PROFILE_ONLY_CHANNEL_FIELDS:
            continue
        if field not in submitted_fields:
            continue
        new_url = str(update_data.get(field) or "").strip()
        old_url = (getattr(hospital, field, None) or "").strip()
        if new_url:
            submitted[field] = (new_url, new_url != old_url)
    return submitted


async def register_channel_sources(
    db: AsyncSession,
    hospital: Hospital,
    submitted_channels: dict[str, tuple[str, bool]],
    *,
    actor: str | None = None,
) -> tuple[list[dict], list[uuid.UUID]]:
    """공식 채널 주소를 근거 자료 행으로 만든다 — 프로파일 저장과 **같은 트랜잭션**에서.

    커밋 뒤에 만들면 그 사이의 실패가 "저장은 됐는데 자료 행은 없는" 병원을 남기고,
    운영자는 같은 값을 다시 저장하기 전까지 그 사실을 모른다. 채널 하나하나는 SAVEPOINT
    안에서 만든다 — 한 채널의 실패가 다른 채널이나 프로파일 저장을 되돌리지 않는다.
    본문 수집은 커밋 뒤 워커가 맡는다. 응답의 QUEUED는 이 트랜잭션이 커밋되면 행이
    있다는 뜻이고, FAILED는 그 SAVEPOINT가 되돌아가 행이 없다는 뜻이다.

    ``actor``가 없으면 요청 actor(사람 PATCH)를 쓴다. 자동 입력 적용처럼 요청 밖에서 부르는
    경로는 ``system:`` actor를 넘긴다.
    """
    actor_name = actor or default_actor()
    entries: list[dict] = []
    dispatch_ids: list[uuid.UUID] = []
    for field, (url, changed) in submitted_channels.items():
        try:
            async with db.begin_nested():
                existing_id = await find_active_source_id_by_url(
                    db, hospital_id=hospital.id, url=url
                )
                created_id: uuid.UUID | None = None
                if existing_id is None:
                    source = await create_pending_channel_source(
                        db,
                        hospital_id=hospital.id,
                        source_type=CHANNEL_SOURCE_TYPES[field],
                        url=url,
                        title=CHANNEL_SOURCE_PROVISIONAL_TITLES[field],
                        channel_field=field,
                        created_by=actor_name,
                    )
                    created_id = source.id
        except Exception:
            logger.warning(
                "Channel source registration failed for hospital %s (%s)",
                hospital.id,
                field,
                exc_info=True,
            )
            entries.append(
                {
                    "field": field,
                    "status": "FAILED",
                    "source_id": None,
                    "message": "자료 등록에 실패했습니다. 자료 화면에서 직접 등록해 주세요.",
                }
            )
            continue
        if existing_id is not None:
            # 값이 그대로면 이번 저장이 한 일이 없다 — 화면에 알릴 것도 없다.
            if changed:
                entries.append(
                    {
                        "field": field,
                        "status": "SKIPPED",
                        "source_id": existing_id,
                        "message": "이미 자료로 등록된 주소입니다.",
                    }
                )
            continue
        dispatch_ids.append(created_id)
        entries.append(
            {"field": field, "status": "QUEUED", "source_id": created_id, "message": None}
        )

    for entry in entries:
        await write_audit_log(
            db,
            action="profile_channel_source_registered",
            hospital_id=hospital.id,
            actor=actor_name,
            target_type="source_asset",
            target_id=entry["source_id"],
            detail={
                "field": entry["field"],
                "status": entry["status"],
                "source_id": str(entry["source_id"]) if entry["source_id"] else None,
                "message": entry["message"],
            },
        )
    return entries, dispatch_ids


def dispatch_channel_source_fetches(source_ids: list[uuid.UUID]) -> None:
    """커밋된 자료 행의 본문 수집을 워커에 넘긴다 — 반드시 커밋 뒤에 부른다.

    발행 실패는 응답을 바꾸지 않는다: 행은 이미 durable하고 `fetch_state`가 QUEUED이므로
    정기 스윕이 같은 fetch를 다시 건다.
    """
    for source_id in source_ids:
        try:
            celery_app.send_task(
                "app.workers.tasks.fetch_channel_source",
                args=[str(source_id)],
                queue="content",
                headers=build_dispatch_headers("fetch-channel-source", str(source_id)),
            )
        except Exception:
            logger.warning(
                "Channel source fetch dispatch failed for source %s", source_id, exc_info=True
            )
