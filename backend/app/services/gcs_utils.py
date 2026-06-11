"""GCS 유틸리티 — Signed URL 생성"""
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

_gcs_client = None


def _get_gcs_client():
    global _gcs_client
    if _gcs_client is None:
        from google.cloud import storage
        _gcs_client = storage.Client()
    return _gcs_client


def get_signed_url(gcs_path: str, expiration_hours: int = 24) -> str:
    """gs://bucket/path → signed URL 변환. 레거시 URL 또는 빈 값은 그대로 통과.

    기본 TTL 24h — /site ISR 캐시(페이지 revalidate 3600s + fetch 캐시 1800s)보다
    충분히 길어야 캐시된 페이지가 만료된 이미지 URL을 서빙하지 않는다.
    """
    if not gcs_path or not gcs_path.startswith("gs://"):
        return gcs_path or ""

    try:
        parts = gcs_path.replace("gs://", "").split("/", 1)
        if len(parts) != 2:
            logger.warning(f"Invalid GCS path format: {gcs_path}")
            return gcs_path

        bucket_name, blob_name = parts[0], parts[1]
        client = _get_gcs_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        expiration = timedelta(hours=expiration_hours)
        try:
            return blob.generate_signed_url(expiration=expiration)
        except Exception:
            # Cloud Run/GCE: ADC에 개인키가 없어 로컬 서명이 불가 — IAM signBlob API로
            # 서명한다. SA에 roles/iam.serviceAccountTokenCreator(자기 자신) 필요
            # (terraform serviceaccount.tf app_self_token_creator).
            import google.auth
            from google.auth.transport import requests as gauth_requests

            credentials, _ = google.auth.default()
            credentials.refresh(gauth_requests.Request())
            return blob.generate_signed_url(
                expiration=expiration,
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )
    except ImportError:
        logger.warning("google-cloud-storage not installed — returning gcs_path as-is")
        return gcs_path
    except Exception as e:
        logger.error(f"Failed to generate signed URL for {gcs_path}: {e}")
        return gcs_path
