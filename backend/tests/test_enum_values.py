from enum import StrEnum

from app.services.enum_values import enum_value


class ExampleState(StrEnum):
    READY = "READY"


def test_enum_value_unwraps_enum_backed_values():
    assert enum_value(ExampleState.READY) == "READY"


def test_enum_value_preserves_plain_values_and_none():
    assert enum_value("READY") == "READY"
    assert enum_value(None) is None
