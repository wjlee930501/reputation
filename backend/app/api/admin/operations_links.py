"""운영 센터로 보내는 admin 주소 — 화면들이 같은 한 곳을 가리키게 하는 순수 함수.

운영 센터 화면은 `/operations` 하나뿐이고 상세는 질의값으로 연다 —
`detail`은 큐 행 id(`incident:{id}`)를 그대로 받는다
(`admin/app/operations/useOperationsCenter.ts`의 `patchQuery({queue, detail: row.id})`).
화면마다 주소를 새로 지으면 링크는 존재하지 않는 경로로 가고, 사람은 404를 본다.
run 전용 화면은 없으므로 실행 실패는 그 병원의 인시던트 큐로 보낸다.
"""

from __future__ import annotations

import uuid


def hospital_incidents_href(hospital_id: uuid.UUID | str) -> str:
    """그 병원의 인시던트 큐 — 상세를 특정할 수 없을 때의 목적지."""
    return f"/operations?queue=incidents&hospital_id={hospital_id}"


def incident_href(hospital_id: uuid.UUID | str, incident_id: uuid.UUID | str) -> str:
    """그 인시던트 상세 — 큐를 열고 같은 행을 곧바로 펼친다."""
    return f"{hospital_incidents_href(hospital_id)}&detail=incident:{incident_id}"
