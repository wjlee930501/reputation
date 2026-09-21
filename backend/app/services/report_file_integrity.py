"""Verify stored report bytes at download/delivery, never on list projections."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from urllib.parse import urlsplit

from app.core.config import settings
from app.services.monthly_report_delivery import safe_local_report_path

logger = logging.getLogger(__name__)
MAX_REPORT_BYTES = 32 * 1024 * 1024


class ReportFileUnavailable(ValueError):
    """Stored bytes cannot be matched to the validated artifact."""


def read_verified_report(path: str, expected_sha256: str, expected_size: int) -> bytes:
    """Read bounded bytes once and return exactly the verified immutable snapshot.

    Call this synchronous storage boundary through a worker thread from async APIs.
    GCS reads are pinned to the observed generation; callers serve these bytes,
    never a subsequently re-opened file or mutable object URL.
    """
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ReportFileUnavailable("Invalid report digest")
    if type(expected_size) is not int or not 0 < expected_size <= MAX_REPORT_BYTES:
        raise ReportFileUnavailable("Invalid report size")
    if not isinstance(path, str) or not path:
        raise ReportFileUnavailable("Missing report path")
    try:
        if path.startswith("gs://"):
            data = _read_cloud_report(path, expected_size)
        else:
            local_path = safe_local_report_path(path)
            if local_path is None:
                raise ReportFileUnavailable("Missing report file")
            with local_path.open("rb") as handle:
                data = handle.read(expected_size + 1)
    except ReportFileUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - bounded storage failure boundary.
        logger.warning("Report storage read failed: %s", type(exc).__name__)
        raise ReportFileUnavailable("Report storage unavailable") from exc
    if len(data) != expected_size or not hmac.compare_digest(
        hashlib.sha256(data).hexdigest(), expected_sha256
    ):
        raise ReportFileUnavailable("Report bytes differ from validated artifact")
    return data


def _read_cloud_report(path: str, expected_size: int) -> bytes:
    from google.cloud import storage

    parsed = urlsplit(path)
    if (
        parsed.scheme != "gs"
        or parsed.netloc != settings.GCS_REPORTS_BUCKET
        or not parsed.path.endswith(".pdf")
        or parsed.query
        or parsed.fragment
        or any(part in ("", ".", "..") for part in parsed.path.lstrip("/").split("/"))
    ):
        raise ReportFileUnavailable("Invalid report storage reference")
    blob = storage.Client().bucket(parsed.netloc).blob(parsed.path.lstrip("/"))
    blob.reload(timeout=15, retry=None)
    generation = blob.generation
    if generation is None or blob.size != expected_size:
        raise ReportFileUnavailable("Report object metadata mismatch")
    return blob.download_as_bytes(
        start=0,
        end=expected_size,
        raw_download=True,
        if_generation_match=generation,
        timeout=20,
        retry=None,
    )
