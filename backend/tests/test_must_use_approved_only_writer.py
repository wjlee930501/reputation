"""작가: 원문 그대로 요구하는 필수 문구는 현재 승인본 문구뿐이다(`generate_content`).

승인본이 2cm→1cm로 바뀐 뒤에도 글의 가이드(brief)에는 옛 2cm 문구가 남아 있을 수 있다. 작가
검증이 그 옛 문구까지 요구하면 철회된 문장을 넣으라고 재작성을 시키게 된다. 금지 표현이 든
승인본 문구는 요구하지 않고, 그 사실을 경고와 운영자 기록으로 남긴다. 공급자는 가짜다.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.models.content import ContentType
from app.services import content_engine
from tests.must_use_review_support import APPROVED_1CM, STALE_BRIEF_2CM
from tests.provider_network_guard_support import (
    forbid_external_network as _forbid_external_network_fixture,  # noqa: F401
)
from tests.test_content_engine_must_use_verbatim import (
    _hospital,
    _install_writer,
    _payload,
    _philosophy,
    _Writer,
)

FORBIDDEN_MESSAGE = "저희 병원은 대장암 완치를 보장합니다."


@pytest.fixture(autouse=True)
def _no_external_calls(forbid_external_network):
    return forbid_external_network


async def test_stale_brief_message_is_not_required_from_the_writer(monkeypatch) -> None:
    writer = _Writer([_payload(APPROVED_1CM)])
    _install_writer(monkeypatch, writer)

    saved = await content_engine.generate_content(
        _hospital(),
        ContentType.NOTICE,
        philosophy=_philosophy([APPROVED_1CM]),
        content_brief={"must_use_messages": [STALE_BRIEF_2CM]},
    )

    assert APPROVED_1CM in saved["body"]
    assert STALE_BRIEF_2CM not in saved["body"]
    # 옛 가이드 문구가 빠졌다는 이유로 재작성하지 않는다.
    assert len(writer.calls) == 1
    assert STALE_BRIEF_2CM not in str(writer.calls[0])


async def test_forbidden_approved_message_is_excluded_with_a_warning_and_record(
    monkeypatch, caplog
) -> None:
    # 기록 모듈이 없는 옛 코드에서는 이 줄이 import 부재로 실패한다(대조군 구분용).
    from app.services import must_use_exclusions

    writer = _Writer([_payload(APPROVED_1CM)])
    _install_writer(monkeypatch, writer)
    opened: list[dict] = []

    async def open_once(**kwargs):
        opened.append(kwargs)
        return True

    monkeypatch.setattr(must_use_exclusions, "_open_once", open_once)
    philosophy = _philosophy([FORBIDDEN_MESSAGE, APPROVED_1CM])
    philosophy.id = uuid.uuid4()
    philosophy.version = 7
    hospital = _hospital()
    caplog.set_level(logging.WARNING)

    saved = await content_engine.generate_content(
        hospital,
        ContentType.NOTICE,
        philosophy=philosophy,
        content_brief={"must_use_messages": [FORBIDDEN_MESSAGE, APPROVED_1CM]},
    )

    assert APPROVED_1CM in saved["body"]
    assert len(writer.calls) == 1
    # 작가 프롬프트의 필수 목록에도 금지 표현 문구는 없다.
    assert FORBIDDEN_MESSAGE not in str(writer.calls[0])
    [log] = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "must_use_message_excluded"
    ]
    assert (log.hospital_id, log.essence_version, log.must_use_index) == (
        str(hospital.id),
        7,
        0,
    )
    assert log.forbidden_expressions
    [record] = opened
    assert record["philosophy_id"] == philosophy.id
    assert record["hospital_id"] == hospital.id
    assert "v7" in record["problem"] and "1번째" in record["problem"]
