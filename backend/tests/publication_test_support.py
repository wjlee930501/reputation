"""Builders for public-content fixtures that carry real reference-check shape."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.models.content import ContentItem
from app.services.reference_verification import (
    item_topic_fingerprint,
    reference_check_record,
)


def verified_reference_checks(
    item: ContentItem, *, checked_at: datetime | None = None
) -> list[dict[str, Any]]:
    """Build URL- and topic-bound successful observations for a fixture article."""

    observed_at = checked_at or datetime.now(UTC)
    checks: list[dict[str, Any]] = []
    for reference in item.references_list or []:
        if not isinstance(reference, Mapping):
            continue
        url = str(reference.get("url") or "").strip()
        if not url:
            continue
        checks.append(
            reference_check_record(
                url,
                verdict="pass",
                reason="page_verified",
                checked_at=observed_at,
                curated=False,
                status=200,
                final_url=url,
                page_title=str(reference.get("title") or ""),
                text_len=900,
                verified_at=observed_at,
                topic_fingerprint=item_topic_fingerprint(item),
            )
        )
    return checks
