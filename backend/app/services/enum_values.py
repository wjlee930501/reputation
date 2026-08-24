"""Pure helpers for values that may be backed by an enum."""

from __future__ import annotations

from typing import Any


def enum_value(value: Any) -> Any:
    """Return an enum-like object's value, or the original plain value."""
    return getattr(value, "value", value)
