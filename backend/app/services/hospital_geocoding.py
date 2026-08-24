"""Bounded address geocoding for profile saves.

One profile save makes at most one provider request. There is deliberately no retry
decorator or fallback cascade: an address typo or provider outage must return a concrete
operator error instead of turning Save into an unbounded external job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

GEOCODE_ENDPOINT = "https://nominatim.openstreetmap.org/search"
GEOCODE_TIMEOUT_SECONDS = 5.0
GEOCODE_RESULT_LIMIT = 1


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str | None = None


class GeocodingError(RuntimeError):
    pass


async def geocode_address(address: str) -> GeocodeResult:
    """Resolve one Korean address with one capped request and no automatic retries."""
    cleaned = " ".join((address or "").split())
    if not cleaned:
        raise GeocodingError("좌표로 변환할 주소를 입력해 주세요.")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(GEOCODE_TIMEOUT_SECONDS),
            follow_redirects=False,
        ) as client:
            response = await client.get(
                GEOCODE_ENDPOINT,
                params={
                    "q": cleaned,
                    "format": "jsonv2",
                    "countrycodes": "kr",
                    "limit": GEOCODE_RESULT_LIMIT,
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ReputationAdmin/1.0 (hospital profile geocoder)",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.RequestError as exc:
        logger.warning("profile geocode unavailable for %s: %s", cleaned, exc)
        raise GeocodingError(
            "주소 좌표 변환 서비스가 응답하지 않습니다. 잠시 후 다시 저장하거나 "
            "고급에서 확인한 좌표를 직접 입력해 주세요."
        ) from exc
    except (httpx.HTTPStatusError, ValueError, TypeError) as exc:
        logger.warning("profile geocode failed for %s: %s", cleaned, exc)
        raise GeocodingError(
            "주소 좌표 변환 결과를 읽지 못했습니다. 도로명과 건물번호를 확인한 뒤 다시 저장하거나 "
            "고급에서 좌표를 직접 입력해 주세요."
        ) from exc

    if not isinstance(payload, list) or not payload:
        raise GeocodingError(
            "입력한 주소에서 좌표를 찾지 못했습니다. 도로명과 건물번호를 포함해 입력하거나 "
            "고급에서 좌표를 직접 입력해 주세요."
        )
    first = payload[0]
    if not isinstance(first, dict):
        raise GeocodingError("주소 좌표 변환 결과 형식이 올바르지 않습니다.")
    try:
        latitude = round(float(first["lat"]), 6)
        longitude = round(float(first["lon"]), 6)
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodingError("주소 좌표 변환 결과에 위도·경도가 없습니다.") from exc
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise GeocodingError("주소 좌표 변환 결과가 허용 범위를 벗어났습니다.")
    display_name = first.get("display_name")
    return GeocodeResult(
        latitude=latitude,
        longitude=longitude,
        display_name=display_name if isinstance(display_name, str) else None,
    )


__all__ = (
    "GEOCODE_RESULT_LIMIT",
    "GeocodeResult",
    "GeocodingError",
    "geocode_address",
)
