#!/usr/bin/env python3
"""외부 URL로 등록된 병원 로고를 우리 자산 저장소로 이관한다.

로고 입력이 URL에서 파일 업로드로 바뀌기 전에 등록된 값은 외부 CDN을 가리킨다.
공개 표면은 우리 저장소·오리진이 아닌 주소를 쓰지 않으므로, 그 병원들의 헤더에는
로고가 뜨지 않고 JSON-LD `logo`도 비어 있다(O-5). 값은 저장돼 있으니 운영자에게는
문제가 보이지 않는다.

이 스크립트는 그 주소에서 이미지를 한 번 받아 우리 저장소에 넣고 `logo_url`을
자산 참조로 바꾼다. 받지 못한 병원은 건드리지 않고 목록으로 보고한다 — 그 병원은
어드민에서 `외부 주소 — 화면에 안 뜸`으로 보이므로 AE가 직접 올리면 된다.

외부 호스트로 나가는 요청이므로 기본은 조회만 하고, 실제 이관은 --apply가 있을
때만 수행한다.

사용:
    python3 scripts/migrate_external_logos.py            # 대상만 조회
    python3 scripts/migrate_external_logos.py --apply    # 실제 이관
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import urlparse

MAX_LOGO_BYTES = 1 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
EXTENSION_BY_TYPE = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
FETCH_TIMEOUT_SECONDS = 20


def _is_external(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return not value.strip().startswith(("gs://", "local://", "/assets/"))


def _fetch_logo(url: str) -> tuple[bytes, str]:
    """로고 바이트와 mime type. 실패하면 RuntimeError."""
    import httpx

    with httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise RuntimeError(f"지원하지 않는 형식: {content_type or '알 수 없음'}")
    data = response.content
    if not data:
        raise RuntimeError("빈 응답")
    if len(data) > MAX_LOGO_BYTES:
        raise RuntimeError(f"{len(data) // 1024}KB — 상한 {MAX_LOGO_BYTES // 1024}KB 초과")
    return data, content_type


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 내려받아 저장한다. 없으면 대상만 보여 준다.",
    )
    args = parser.parse_args()

    # 앱 패키지를 쓰므로 backend/ 안에서 실행하거나 PYTHONPATH에 backend를 넣어야 한다.
    from sqlalchemy import select

    from app.core.database import SyncSessionLocal
    from app.models.hospital import Hospital
    from app.services.asset_storage import store_asset_bytes

    migrated: list[str] = []
    failed: list[tuple[str, str]] = []

    with SyncSessionLocal() as db:
        hospitals = db.execute(select(Hospital)).scalars().all()
        targets = [h for h in hospitals if _is_external(h.logo_url)]

        if not targets:
            print("외부 URL 로고를 쓰는 병원이 없습니다.")
            return 0

        print(f"대상 {len(targets)}곳:")
        for hospital in targets:
            print(f"  - {hospital.name}: {hospital.logo_url}")

        if not args.apply:
            print("\n조회만 했습니다. 실제 이관은 --apply를 붙여 실행하세요.")
            return 0

        for hospital in targets:
            source_url = (hospital.logo_url or "").strip()
            try:
                data, content_type = _fetch_logo(source_url)
                extension = EXTENSION_BY_TYPE[content_type]
                stored_ref = store_asset_bytes(
                    hospital_id=hospital.id,
                    filename=f"logo{extension}",
                    data=data,
                    mime_type=content_type,
                )
            except Exception as exc:  # noqa: BLE001 — 한 병원 실패가 나머지를 막지 않는다
                failed.append((hospital.name, str(exc)))
                continue
            hospital.logo_url = stored_ref
            migrated.append(hospital.name)
        db.commit()

    print(f"\n이관 완료 {len(migrated)}곳: {', '.join(migrated) or '없음'}")
    if failed:
        print(f"이관 실패 {len(failed)}곳 — 어드민에서 직접 업로드해 주세요:")
        for name, reason in failed:
            print(f"  - {name}: {reason}")
    print("\n공개 화면 반영을 위해 각 병원의 사이트 갱신이 필요합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
