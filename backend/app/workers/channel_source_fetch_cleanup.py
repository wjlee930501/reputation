"""채널 자료 fetch 실패가 남긴 인시던트를 걷어 내는 한도 있는 멱등 정리.

채널 URL의 본문을 가져오지 못한 것은 예외 상황이 아니라 자료 행의 상태다(ERROR +
`fetch_error`). 사람 경로는 필수 자료가 비었다고 말하는 콘텐츠 상태 카드가 이미 맡는다.
그래서 `CHANNEL_SOURCE_FETCH_FAILED`는 더 열지 않고, 이미 열린 것은 스윕이 이 pass로
닫는다.

- 스캔 1: 자료 행의 `source_metadata.incident_id`가 가리키는 인시던트의 `incident_type`이
  `CHANNEL_SOURCE_FETCH_FAILED`이면 **인시던트 상태와 무관하게** 링크를 떼고
  `incident_retired_at`을 남긴다. 이미 RECOVERED/ACKNOWLEDGED인 옛 링크도 같이 걷힌다.
- 스캔 2: 자료 행이 가리키지 않는 열린 fetch 인시던트도 같은 상한 안에서 닫는다. 링크만
  보면 처음부터 링크가 없었거나 링크가 먼저 걷힌 인시던트가 영원히 열린 채 남는다.
- 아직 OPEN/RETRYING인 인시던트만 RECOVERED로 내린다. Slack·NotificationOutbox는 만들지
  않는다 — 사람에게 알릴 새 사실이 없다.
- 닫지 못한(다른 쓰기와 겹친) 인시던트의 링크는 떼지 않는다. 링크가 곧 발견 경로이므로,
  먼저 떼면 열린 인시던트가 다음 pass에서 자료 행으로는 보이지 않는다.
- 한 pass에 상한을 두고 두 스캔이 그 상한을 나눠 쓴다. 링크를 뗀 행은 다음 pass의 후보가
  아니므로 반복하면 수렴한다. 혼합 버전 롤아웃 중 옛 워커가 다시 붙인 링크도 다음 pass가
  걷는다.
- `SOURCE_PROCESSING_FAILED` 링크는 건드리지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import String, cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.essence import HospitalSourceAsset
from app.models.operations import Incident, IncidentState
from app.services.incidents import mark_recovered, mark_retrying

CHANNEL_SOURCE_FETCH_INCIDENT_TYPE = "CHANNEL_SOURCE_FETCH_FAILED"
CHANNEL_SOURCE_FETCH_CLEANUP_ACTOR = "system:channel-source-sweep"
CHANNEL_SOURCE_FETCH_CLEANUP_REASON = "policy: source fetch failures are source state"
CHANNEL_SOURCE_FETCH_CLEANUP_LIMIT = 200

# 자료 행 전체를 읽어 다시 쓰면 그 사이의 다른 쓰기(처리 인시던트 링크, fetch 계수)를
# 덮는다. 관측한 키 하나만 지우고 회수 시각을 덧씌우되, 그 키가 아직 같은 값일 때만
# 쓴다 — 0행이면 다른 쪽이 이미 바꾼 것이므로 조용히 넘긴다.
RETIRE_SOURCE_LINK_SQL = text(
    "UPDATE hospital_source_assets "
    "SET source_metadata = (source_metadata - 'incident_id') "
    # asyncpg는 jsonb_build_object의 인자 타입을 추론하지 못한다 — 명시적으로 말해 준다.
    "|| jsonb_build_object('incident_retired_at', CAST(:retired_at AS text)) "
    "WHERE id = :source_id AND source_metadata->>'incident_id' = :incident_id"
)


async def retire_channel_source_fetch_incidents(
    db: AsyncSession, *, limit: int = CHANNEL_SOURCE_FETCH_CLEANUP_LIMIT
) -> dict[str, int]:
    """한 pass 분량의 fetch 인시던트를 걷는다. 상한은 두 스캔이 나눠 쓴다.

    스캔 1은 자료 행에 링크된 인시던트, 스캔 2는 링크가 없는(또는 이미 걷힌) 열린
    인시던트다. 자료 metadata만 보면 링크 없이 열린 인시던트가 영원히 남는다.
    """

    retired_at = datetime.now(UTC).isoformat()
    linked_retired, recovered, seen = await _retire_linked(db, limit, retired_at)
    recovered += await _recover_unlinked(db, limit - len(seen), seen)
    await db.commit()
    return {"links_retired": linked_retired, "incidents_recovered": recovered}


async def _retire_linked(
    db: AsyncSession, limit: int, retired_at: str
) -> tuple[int, int, set[uuid.UUID]]:
    """자료 행이 가리키는 fetch 인시던트를 걷는다. 걷은 링크 수·복구 수·본 인시던트."""

    # uuid 컬럼을 텍스트로 캐스팅해 비교한다 — 반대로 metadata 문자열을 uuid로 캐스팅하면
    # 옛 데이터의 잘못된 값 하나가 질의 전체를 실패시킨다.
    pairs = (
        (
            await db.execute(
                select(HospitalSourceAsset, Incident)
                .join(
                    Incident,
                    cast(Incident.id, String)
                    == HospitalSourceAsset.source_metadata["incident_id"].as_string(),
                )
                .where(Incident.incident_type == CHANNEL_SOURCE_FETCH_INCIDENT_TYPE)
                .order_by(HospitalSourceAsset.created_at.asc())
                .limit(max(limit, 0))
            )
        )
        .all()
        if limit > 0
        else []
    )

    retired = 0
    recovered = 0
    seen: set[uuid.UUID] = set()
    for source, incident in pairs:
        seen.add(incident.id)
        outcome = await _recover_without_notice(db, incident)
        if outcome == "conflict":
            # 아직 열린 인시던트를 닫지 못했다. 링크를 남겨 두어야 다음 pass가 이 행을
            # 다시 찾는다 — 여기서 떼면 인시던트만 열린 채 발견되지 않는다.
            continue
        if outcome == "recovered":
            recovered += 1
        result = await db.execute(
            RETIRE_SOURCE_LINK_SQL,
            {
                "retired_at": retired_at,
                "source_id": source.id,
                "incident_id": str(incident.id),
            },
        )
        retired += int(result.rowcount or 0)
    return retired, recovered, seen


async def _recover_unlinked(
    db: AsyncSession, limit: int, seen: set[uuid.UUID]
) -> int:
    """자료 행이 가리키지 않는 열린 fetch 인시던트도 같은 방식으로 닫는다."""

    if limit <= 0:
        return 0
    predicates = [
        Incident.incident_type == CHANNEL_SOURCE_FETCH_INCIDENT_TYPE,
        Incident.state.in_((IncidentState.OPEN.value, IncidentState.RETRYING.value)),
    ]
    if seen:
        predicates.append(Incident.id.notin_(seen))
    incidents = list(
        (
            await db.execute(
                select(Incident)
                .where(*predicates)
                .order_by(Incident.first_seen_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    recovered = 0
    for incident in incidents:
        if await _recover_without_notice(db, incident) == "recovered":
            recovered += 1
    return recovered


async def _recover_without_notice(db: AsyncSession, incident: Incident) -> str:
    """OPEN/RETRYING 인시던트만 RECOVERED로 내린다(알림 없음).

    `recovered`는 이 pass가 닫았다는 뜻, `closed`는 이미 닫혀 있어 할 일이 없다는 뜻,
    `conflict`는 다른 쓰기와 겹쳐 아직 열려 있다는 뜻이다. 셋을 구분해야 링크를 언제
    떼도 되는지 판단할 수 있다.
    """

    current = incident
    if current.state in {IncidentState.RECOVERED.value, IncidentState.ACKNOWLEDGED.value}:
        return "closed"
    if current.state == IncidentState.OPEN.value:
        retrying = await mark_retrying(
            db,
            current.id,
            expected_version=current.version,
            actor=CHANNEL_SOURCE_FETCH_CLEANUP_ACTOR,
            reason=CHANNEL_SOURCE_FETCH_CLEANUP_REASON,
        )
        if not isinstance(retrying, Incident):
            return "conflict"
        current = retrying
    if current.state != IncidentState.RETRYING.value:
        return "conflict"
    recovered = await mark_recovered(
        db,
        current.id,
        expected_version=current.version,
        observed_success=True,
        actor=CHANNEL_SOURCE_FETCH_CLEANUP_ACTOR,
        reason=CHANNEL_SOURCE_FETCH_CLEANUP_REASON,
    )
    return "recovered" if isinstance(recovered, Incident) else "conflict"
