"""이 서비스가 직접 서빙하는 공개 자료 파일 경로의 정본 모양.

Admin의 '대표 이미지로 지정'은 절대 URL이 아니라 이 상대 경로를 저장하고, 공개 표면은
같은 경로를 그대로 내려보낸다. 저장 검증과 공개 직렬화가 서로 다른 느슨한 검사(접두사·
부분 문자열)를 쓰면 `//host/...`나 `../` 조작이 한쪽만 통과한다. 그래서 정확히 한 모양만
정의하고 양쪽이 같은 것을 쓴다.
"""

import re

#: `/api/v1/public/hospitals/{slug}/assets/{uuid}` — slug와 UUID 이외의 글자는 받지 않는다.
PUBLIC_ASSET_PATH_RE = re.compile(
    r"/api/v1/public/hospitals/[A-Za-z0-9._-]+/assets/[0-9a-fA-F-]{36}"
)


def is_public_asset_path(value: str | None) -> bool:
    """공개 자료 파일 라우트의 상대 경로인지."""
    if not value:
        return False
    return PUBLIC_ASSET_PATH_RE.fullmatch(value.strip()) is not None


__all__ = ("PUBLIC_ASSET_PATH_RE", "is_public_asset_path")
