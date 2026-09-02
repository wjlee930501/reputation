"""Stable bounded idempotency keys for operational commands and retries."""

import hashlib
import uuid


def normalize_operation_key(value: str | None) -> str | None:
    return (value or "").strip()[:255] or None


def retry_operation_key(parent_id: uuid.UUID, request_key: str) -> str | None:
    normalized = request_key.strip()
    if not normalized:
        return None
    candidate = f"retry:{parent_id}:{normalized}"
    if len(candidate) <= 255:
        return candidate
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"retry:{parent_id}:sha256:{digest}"
