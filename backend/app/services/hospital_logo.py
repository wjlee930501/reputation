"""공식 로고 자산 정책 — 저장 가능한 형태와 공개 표면이 실제로 쓸 수 있는 형태.

L-1 배경. 온보딩 2단계는 공식 로고를 필수로 요구하는데, 어드민은 아무 URL이나 받고
공개 표면(site/lib/hospital-payload.ts의 `resolveAssetUrl`)은 자기 백엔드·GCS 버킷이
아닌 주소를 조용히 버렸다. 그래서 운영자는 병원 홈페이지 CDN의 로고를 핫링크해 두고
`승인됨` 배지를 받았지만, 공개 화면 헤더에는 `<img>`가 아예 없었다 — 효과가 없는 입력을
필수로 강제하는 상태였다.

여기서 정하는 계약:
  - 저장은 **업로드된 자산 참조**만 받는다(`gs://`, `local://`, 레거시 `/assets/...`).
  - 그 참조는 공개 라우트(`/api/v1/public/hospitals/{slug}/logo`)를 통해서만 노출된다.
    백엔드 오리진 경로라 공개 표면의 자산 허용 목록을 그대로 통과한다.
  - 외부 http(s) 주소는 저장 단계에서 막는다. 조용히 버려지는 값을 `승인됨`으로
    표시하지 않기 위해서다.
"""

from urllib.parse import urlparse

# store_asset_bytes가 만드는 참조 + 과거 업로드가 남긴 경로.
_STORED_ASSET_PREFIXES = ("gs://", "local://", "/assets/")

LOGO_MAX_BYTES = 1 * 1024 * 1024

# 헤더 로고로 쓸 수 있는 형식만. SVG는 스크립트를 품을 수 있어 받지 않는다.
LOGO_ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

EXTERNAL_LOGO_URL_MESSAGE = (
    "외부 사이트의 로고 주소는 공개 화면에 쓸 수 없습니다. "
    "로고 파일을 직접 업로드해 주세요."
)


def is_stored_logo_ref(value: str | None) -> bool:
    """업로드로 저장돼 공개 라우트로 서빙 가능한 참조인지."""
    if not value:
        return False
    return value.strip().startswith(_STORED_ASSET_PREFIXES)


def is_external_logo_url(value: str | None) -> bool:
    """공개 표면이 쓸 수 없는 외부 http(s) 주소인지."""
    if not value:
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def public_logo_url(slug: str) -> str:
    """공개 표면이 읽는 로고 주소. 백엔드 오리진 경로라 자산 허용 목록을 통과한다."""
    return f"/api/v1/public/hospitals/{slug}/logo"
