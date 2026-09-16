"""One snapshot parser protects both baseline lookup and resumed provider inputs."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from app.services.v0_measurement_snapshot import (
    local_v0_query_texts,
    parse_v0_judgment_context,
    v0_platforms_from_run,
    v0_queries_from_snapshot,
    v0_query_snapshot,
    v0_resume_judgment_context,
)

Q1, Q2 = UUID(int=1), UUID(int=2)


def row(
    query_id: UUID = Q1, text: Any = "  서울 회복 안내  ", intent: Any = "LOCAL"
) -> dict[str, Any]:
    return {"query_id": str(query_id), "query_text": text, "query_intent": intent}


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        (),
        [],
        [None],
        ["query"],
        [{}],
        [{"query_id": "not-a-uuid", "query_text": "text", "query_intent": "LOCAL"}],
        [row(), row()],
        [row(text="   ")],
        [row(text=1)],
        [row(text=None)],
        [row(intent=None)],
        [row(intent=1)],
    ],
)
def test_malformed_snapshot_is_rejected_consistently_before_querying(snapshot: object) -> None:
    db = Mock()
    original = deepcopy(snapshot)
    assert local_v0_query_texts(snapshot) is None
    assert v0_queries_from_snapshot(db, snapshot, hospital_id=uuid4()) is None
    assert snapshot == original
    db.execute.assert_not_called()


def test_resume_keeps_original_order_and_text_after_live_query_edit() -> None:
    hospital_id = uuid4()
    snapshot = [row(Q2, "  두 번째 원본  "), row(Q1, "첫 번째 원본", "INFO")]
    before = deepcopy(snapshot)
    live = [
        SimpleNamespace(id=qid, hospital_id=hospital_id, query_text="수정된 현재 값")
        for qid in (Q1, Q2)
    ]
    db = Mock()
    db.execute.return_value.scalars.return_value = live
    restored = v0_queries_from_snapshot(db, snapshot, hospital_id=hospital_id)
    assert [(query.id, query.query_text, query.query_intent) for query in restored] == [
        (Q2, "  두 번째 원본  ", "LOCAL"),
        (Q1, "첫 번째 원본", "INFO"),
    ]
    assert local_v0_query_texts(snapshot) == {Q2: "  두 번째 원본  "}
    assert snapshot == before
    assert all(query.query_text == "수정된 현재 값" for query in live)
    db.commit.assert_not_called()


@pytest.mark.parametrize("condition", ["missing", "other_hospital", "extra_row"])
def test_resume_requires_exact_live_ownership(condition: str) -> None:
    hospital_id = uuid4()
    live = [SimpleNamespace(id=Q1, hospital_id=hospital_id)]
    if condition == "missing":
        live = []
    elif condition == "other_hospital":
        live[0].hospital_id = uuid4()
    else:
        live.append(SimpleNamespace(id=Q2, hospital_id=hospital_id))
    db = Mock()
    db.execute.return_value.scalars.return_value = live
    assert v0_queries_from_snapshot(db, [row()], hospital_id=hospital_id) is None


@pytest.mark.parametrize("intent", ["INFO", "", "local"])
def test_nonlocal_string_intent_is_valid_for_resume_but_not_local_baseline(intent: str) -> None:
    hospital_id = uuid4()
    db = Mock()
    db.execute.return_value.scalars.return_value = [SimpleNamespace(id=Q1, hospital_id=hospital_id)]
    assert local_v0_query_texts([row(intent=intent)]) is None
    restored = v0_queries_from_snapshot(db, [row(intent=intent)], hospital_id=hospital_id)
    assert restored[0].query_intent == intent


def test_serialized_snapshot_and_context_are_detached_from_mutable_fields() -> None:
    query = SimpleNamespace(id=Q1, query_text="처음 질문", query_intent="LOCAL")
    snapshot = v0_query_snapshot([query])
    query.query_text = "새 질문"
    assert snapshot[0]["query_text"] == "처음 질문"
    hospital_id = uuid4()
    saved = {
        "hospital_identity": str(hospital_id),
        "hospital_name": "원래 병원",
        "region": "서울",
        "competitors": ["이웃 병원"],
    }
    parsed = parse_v0_judgment_context(saved, hospital_id=hospital_id)
    parsed["competitors"].append("추가 병원")
    assert saved["competitors"] == ["이웃 병원"]


@pytest.mark.parametrize(
    "models,expected",
    [
        ({"gemini": "g", "chatgpt": "o"}, ["chatgpt", "gemini"]),
        ({"chatgpt": "o"}, ["chatgpt"]),
        ({}, None),
        ({"unknown": "x"}, None),
        ({"chatgpt": "o", "unknown": "x"}, None),
    ],
)
def test_frozen_provider_set_rejects_unrecognized_platforms(
    models: dict, expected: list[str] | None
) -> None:
    assert v0_platforms_from_run(SimpleNamespace(config={"model_names": models})) == expected


def test_frozen_judgment_context_wins_over_current_profile() -> None:
    hospital_id = uuid4()
    saved = {
        "hospital_identity": str(hospital_id),
        "hospital_name": "원래 병원",
        "region": "서울",
        "competitors": [],
    }
    run = SimpleNamespace(config={"judgment_context": deepcopy(saved)})
    current = SimpleNamespace(
        id=hospital_id, name="바뀐 병원", region=["부산"], competitors=["다른 병원"]
    )
    assert v0_resume_judgment_context(run, current, [], {}) == saved
    assert current.name == "바뀐 병원"
