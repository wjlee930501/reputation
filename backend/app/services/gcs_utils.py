"""GCS 유틸리티 — Signed URL 생성"""
import logging
import threading
import time
from datetime import timedelta

logger = logging.getLogger(__name__)

_gcs_client = None

# Signed URL in-process TTL 캐시 (R9): admin/public 목록 serializer가 아이템마다
# get_signed_url을 호출하는데, Cloud Run(keyless ADC)에서는 호출당 IAM signBlob
# 네트워크 왕복이 발생해 페이지당 16+회 순차 RTT가 쌓인다. 서명 수명(기본 24h)의
# 절반(12h) 동안 같은 경로의 URL을 재사용한다 — 캐시에서 꺼낸 URL도 최소 12h의
# 잔여 수명을 보장.
SIGNED_URL_CACHE_TTL_SECONDS = 12 * 3600
SIGNED_URL_CACHE_MAX_ENTRIES = 1024

_signed_url_cache: dict[tuple[str, int], tuple[float, str]] = {}
_signed_url_cache_lock = threading.Lock()

# Cloud Run keyless ADC credentials — 모듈 수준 캐시 (R9). 호출마다
# google.auth.default() + refresh()를 돌리면 서명 1건당 토큰 발급 왕복이 추가된다.
_adc_credentials = None
_adc_lock = threading.Lock()


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage
        _gcs_client = storage.Client()
    return _gcs_client


def _get_iam_signing_credentials():
    """IAM signBlob용 ADC credentials — 캐시하고 만료(임박) 시에만 refresh."""
    global _adc_credentials
    import google.auth
    from google.auth.transport import requests as gauth_requests

    with _adc_lock:
        if _adc_credentials is None:
            _adc_credentials, _ = google.auth.default()
        # google.auth credentials.valid == (token 있음 and not expired). expired는
        # 실제 만료 전 여유(skew)를 두고 True가 되므로 만료 임박 시 선제 갱신된다.
        if not getattr(_adc_credentials, "valid", False):
            _adc_credentials.refresh(gauth_requests.Request())
        return _adc_credentials


def _signed_url_cache_get(key: tuple[str, int]) -> str | None:
    now = time.monotonic()
    with _signed_url_cache_lock:
        entry = _signed_url_cache.get(key)
        if entry is None:
            return None
        expires_at, url = entry
        if expires_at <= now:
            _signed_url_cache.pop(key, None)
            return None
        return url


def _signed_url_cache_put(key: tuple[str, int], url: str, ttl_seconds: float) -> None:
    with _signed_url_cache_lock:
        if key not in _signed_url_cache and len(_signed_url_cache) >= SIGNED_URL_CACHE_MAX_ENTRIES:
            # 단순 size cap: 가장 오래 전에 들어온 항목부터 제거 (dict 삽입 순서).
            evict_count = max(1, SIGNED_URL_CACHE_MAX_ENTRIES // 10)
            for stale_key in list(_signed_url_cache)[:evict_count]:
                _signed_url_cache.pop(stale_key, None)
        _signed_url_cache[key] = (time.monotonic() + ttl_seconds, url)


def get_signed_url(gcs_path: str | None, expiration_hours: int = 24) -> str:
    """gs://bucket/path → signed URL 변환. 레거시 URL 또는 빈 값은 그대로 통과.

    기본 TTL 24h — /site ISR 캐시(페이지 revalidate 3600s + fetch 캐시 1800s)보다
    충분히 길어야 캐시된 페이지가 만료된 이미지 URL을 서빙하지 않는다.
    """
    if not gcs_path or not gcs_path.startswith("gs://"):
        return gcs_path or ""

    cache_key = (gcs_path, expiration_hours)
    cached = _signed_url_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        parts = gcs_path.replace("gs://", "").split("/", 1)
        if len(parts) != 2:
            logger.warning("Invalid GCS path format: %s", gcs_path)
            return ""

        bucket_name, blob_name = parts[0], parts[1]
        client = _get_gcs_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        expiration = timedelta(hours=expiration_hours)
        try:
            signed_url = blob.generate_signed_url(expiration=expiration)
        except Exception:
            # Cloud Run/GCE: ADC에 개인키가 없어 로컬 서명이 불가 — IAM signBlob API로
            # 서명한다. SA에 roles/iam.serviceAccountTokenCreator(자기 자신) 필요
            # (terraform serviceaccount.tf app_self_token_creator).
            credentials = _get_iam_signing_credentials()
            signed_url = blob.generate_signed_url(
                expiration=expiration,
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )
        # 캐시 TTL은 서명 수명의 절반을 넘지 않게 — 짧은 expiration_hours 호출이
        # 만료된 URL을 캐시에서 서빙하는 일이 없도록 한다.
        ttl = min(SIGNED_URL_CACHE_TTL_SECONDS, expiration_hours * 3600 / 2)
        _signed_url_cache_put(cache_key, signed_url, ttl)
        return signed_url
    except ImportError:
        logger.warning("google-cloud-storage not installed — suppressing unsigned GCS path")
        return ""
    except Exception as e:
        logger.error("Failed to generate signed URL for %s: %s", gcs_path, e)
        return ""
