"""Typed hospital-specific editorial scope shared by generation and publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MAX_CONTENT_FOCUS_TOPICS = 8
MAX_CONTENT_FOCUS_TOPIC_LENGTH = 40


@dataclass(frozen=True, slots=True)
class ContentFocusOutOfScopeError(ValueError):
    selected_topic: str | None
    allowed_topics: tuple[str, ...]

    def __str__(self) -> str:
        selected = self.selected_topic or "missing"
        return f"content focus topic {selected!r} is outside {self.allowed_topics!r}"


def normalize_content_focus_topics(values: Sequence[str] | None) -> tuple[str, ...]:
    """Return a bounded, ordered allowlist without inventing topic aliases."""
    normalized: list[str] = []
    for value in values or ():
        cleaned = " ".join(value.split())[:MAX_CONTENT_FOCUS_TOPIC_LENGTH]
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return tuple(normalized[:MAX_CONTENT_FOCUS_TOPICS])


def validate_generated_content_focus(
    payload: Mapping[str, str | None],
    allowed_topics: Sequence[str] | None,
) -> str | None:
    """Require the provider to select one exact operator-approved topic."""
    normalized_topics = normalize_content_focus_topics(allowed_topics)
    if not normalized_topics:
        return None
    raw_topic = payload.get("content_focus_topic")
    selected_topic = " ".join(raw_topic.split()) if isinstance(raw_topic, str) else None
    if selected_topic not in normalized_topics:
        raise ContentFocusOutOfScopeError(selected_topic, normalized_topics)
    if matching_content_focus_topic(
        (payload.get("title"), payload.get("body")),
        (selected_topic,),
    ) is None:
        raise ContentFocusOutOfScopeError(selected_topic, normalized_topics)
    return selected_topic


def matching_content_focus_topic(
    values: Sequence[str | None],
    allowed_topics: Sequence[str] | None,
) -> str | None:
    """Return the first approved topic grounded in operator-visible content."""
    searchable = " ".join(
        " ".join(value.split()).casefold() for value in values if isinstance(value, str)
    )
    return next(
        (topic for topic in normalize_content_focus_topics(allowed_topics) if topic.casefold() in searchable),
        None,
    )
