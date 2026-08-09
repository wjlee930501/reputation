"""Stable bounded idempotency keys for operational commands and retries."""

import hashlib
import re
import uuid

_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")


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


def safe_task_id(value: str | None) -> str | None:
    rendered = str(value) if value is not None else None
    return rendered if rendered is not None and _SAFE_TASK_ID.fullmatch(rendered) else None
