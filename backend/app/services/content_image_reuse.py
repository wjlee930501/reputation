"""대표 이미지를 만들지 못한 글에 **같은 병원의 이미 인증된 이미지**를 빌려주는 경로.

왜 필요한가: 이미지 실패는 표본 실패다. 하룻밤 예산을 다 써도 본문은 완성돼 있고,
계약은 그 글의 발행을 기다린다. 그렇다고 이미지 없이 내보내지는 않는다 — 대신 그 병원이
이미 공개 중인 글의 인증된 이미지 하나를 빌려 발행하고, 사후 교체 스윕
(`workers/published_image_refresh`)이 그 글의 주제 이미지를 만들어 바꿔 단다.

계약:

- 합성 인증값을 만들지 않는다. 빌려온 행에는 원본의 내용 hash·주제 hash·정책 버전·검수
  시각을 **그대로** 옮기고 `image_reused_from_content_id`로 출처를 명시한다. 새 제목으로
  주제 hash를 다시 계산하면 아무도 검수하지 않은 값이 인증처럼 보이게 된다.
- 빌려줄 수 있는 원본은 지금 인증이 현재인 **공개 중(PUBLISHED)** 글뿐이다. 빌려온
  이미지는 다시 빌려주지 않는다(재사용의 연쇄로 한 장이 병원 전체로 번지지 않게 한다).
- 가장 오래 전에 인증된 이미지부터 빌린다. 최근 글일수록 사람 눈에 자주 보이므로 같은
  이미지가 이웃한 두 글에 나란히 붙는 상황을 피한다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update

from app.models.content import ContentItem, ContentStatus
from app.services.content_publication import image_certification_current

# 생성 결과를 되쓸 수 있는 상태와 같다 — 공개 뒤의 행은 이 경로로 바꾸지 않는다.
REUSE_WRITE_BACK_STATUSES = (
    ContentStatus.DRAFT,
    ContentStatus.REJECTED,
    ContentStatus.READY,
)

# 한 번의 선택을 위해 읽는 후보 상한. 병원 하나의 공개 글 수는 수백 단위이므로
# 전량을 읽지 않고 오래된 순으로 필요한 만큼만 본다.
REUSE_CANDIDATE_SCAN_LIMIT = 50


def _candidate_stmt(hospital_id: uuid.UUID, exclude_item_id: uuid.UUID | None):
    predicates = [
        ContentItem.hospital_id == hospital_id,
        ContentItem.status == ContentStatus.PUBLISHED,
        ContentItem.image_url.is_not(None),
        ContentItem.image_policy_verified_at.is_not(None),
        # 빌려온 이미지는 다시 빌려주지 않는다.
        ContentItem.image_reused_from_content_id.is_(None),
    ]
    if exclude_item_id is not None:
        predicates.append(ContentItem.id != exclude_item_id)
    return (
        select(ContentItem)
        .where(*predicates)
        .order_by(
            ContentItem.image_policy_verified_at.asc(),
            ContentItem.generated_at.asc(),
            ContentItem.id.asc(),
        )
        .limit(REUSE_CANDIDATE_SCAN_LIMIT)
    )


def select_reusable_hospital_image(
    db: Any,
    hospital_id: uuid.UUID,
    exclude_item_id: uuid.UUID | None = None,
) -> ContentItem | None:
    """빌려올 수 있는 가장 오래된 인증 이미지를 가진 공개 글. 없으면 None.

    SQL 조건은 후보를 좁히기만 한다 — 실제 통과 여부는 발행·공개와 **같은 함수**
    (`image_certification_current`)가 행 단위로 판정한다. 두 판정이 갈라지면 공개 게이트가
    거부할 이미지를 빌려주게 된다.
    """

    candidates = db.execute(_candidate_stmt(hospital_id, exclude_item_id)).scalars().all()
    for candidate in candidates:
        if image_certification_current(candidate):
            return candidate
    return None


def apply_reused_image(
    db: Any,
    *,
    item: ContentItem,
    source: ContentItem,
    expected_revision: int | None = None,
    expected_claim_token: uuid.UUID | None = None,
) -> int:
    """빌려온 인증을 가드와 함께 쓴다. 반환값은 갱신된 행 수(0이면 호출부는 버린다).

    `content_revision`은 생성 write-back과 같은 규칙으로 다루지 않는다 — 이 경로는
    본문 후보를 바꾸지 않으므로 판을 올리지 않고, 대신 판이 그대로일 때만 쓴다.
    """

    predicates = [
        ContentItem.id == item.id,
        ContentItem.status.in_(REUSE_WRITE_BACK_STATUSES),
    ]
    if expected_revision is not None:
        predicates.append(ContentItem.content_revision == expected_revision)
    if expected_claim_token is not None:
        predicates.append(ContentItem.generation_claim_token == expected_claim_token)
    result = db.execute(
        update(ContentItem)
        .where(*predicates)
        .values(
            image_url=source.image_url,
            # 프롬프트는 원본 글의 주제로 쓰인 것이다. 이 글의 것처럼 남기지 않는다.
            image_prompt=None,
            image_content_hash=source.image_content_hash,
            # 원본의 주제 결합을 그대로 들고 간다(합성값 금지). 결합 대상은 marker가 말한다.
            image_subject_hash=source.image_subject_hash,
            image_policy_version=source.image_policy_version,
            image_policy_verified_at=source.image_policy_verified_at,
            image_reused_from_content_id=source.id,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
