"""GCS 유틸리티 — Signed URL 생성"""
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)


def get_signed_url(gcs_path: str, expiration_hours: int = 1) -> str:
    """gs://bucket/path → signed URL 변환. 레거시 URL 또는 빈 값은 그대로 통과."""
    if not gcs_path or not gcs_path.startswith("gs://"):
        return gcs_path or ""

    try:
        from google.cloud import storage

        parts = gcs_path.replace("gs://", "").split("/", 1)
        if len(parts) != 2:
            logger.warning(f"Invalid GCS path format: {gcs_path}")
            return gcs_path

        bucket_name, blob_name = parts[0], parts[1]
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        return blob.generate_signed_url(expiration=timedelta(hours=expiration_hours))
    except ImportError:
        logger.warning("google-cloud-storage not installed — returning gcs_path as-is")
        return gcs_path
    except Exception as e:
        logger.error(f"Failed to generate signed URL for {gcs_path}: {e}")
        return gcs_path
