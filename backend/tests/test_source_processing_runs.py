import uuid
from types import SimpleNamespace

from app.models.essence import SourceType
from app.services.source_processing_runs import (
    SOURCE_PROCESSING_METADATA_KEY,
    client_source_metadata,
    merge_source_metadata_patch,
    processing_claim,
    processing_input_hash,
    run_dispatch_matches,
    source_patch_changed_fields,
    source_processing_reservation_id,
    source_run_key,
)


def _source(**overrides):
    values = {
        "source_type": SourceType.HOMEPAGE,
        "title": "원문 자료",
        "url": "https://example.com",
        "raw_text": "원문",
        "operator_note": None,
        "source_metadata": {"channel": "homepage"},
        "updated_by": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_patch_same_normalized_values_is_true_noop() -> None:
    source = _source()

    changed = source_patch_changed_fields(
        source,
        {
            "source_type": SourceType.HOMEPAGE,
            "title": "  원문 자료  ",
            "url": "  https://example.com  ",
            "raw_text": "원문",
            "operator_note": "   ",
            "source_metadata": {"channel": "homepage"},
        },
    )

    assert changed == set()


def test_client_metadata_cannot_remove_or_replace_processing_claim() -> None:
    claim = {"token": "server-token", "input_hash": "hash"}
    current = {
        "channel": "homepage",
        SOURCE_PROCESSING_METADATA_KEY: claim,
        "extraction_coverage": {"complete": True},
    }

    merged = merge_source_metadata_patch(
        current,
        {
            "channel": "edited",
            SOURCE_PROCESSING_METADATA_KEY: {"token": "client-token"},
        },
    )

    assert processing_claim(merged) == claim
    assert merged["channel"] == "edited"
    assert merged["extraction_coverage"] == {"complete": True}


def test_create_metadata_drops_every_server_owned_processing_field() -> None:
    merged = merge_source_metadata_patch(
        {},
        {
            "channel": "interview",
            SOURCE_PROCESSING_METADATA_KEY: {"token": "forged"},
            "extraction_input_hash": "forged",
            "extraction_coverage": {"complete": True},
        },
    )

    assert merged == {"channel": "interview"}


def test_processing_identity_changes_with_material_metadata_and_version() -> None:
    first = processing_input_hash(_source(), "content-hash")
    second = processing_input_hash(_source(source_metadata={"channel": "blog"}), "content-hash")

    source_id = uuid.uuid4()
    assert first != second
    assert source_run_key([f"{source_id}:{first}"]) != source_run_key(
        [f"{source_id}:{second}"]
    )


def test_client_metadata_hides_processing_fences_but_keeps_coverage() -> None:
    public = client_source_metadata(
        {
            "channel": "homepage",
            SOURCE_PROCESSING_METADATA_KEY: {"token": "secret"},
            "extraction_input_hash": "private-input-hash",
            "extraction_coverage": {"complete": True},
        }
    )

    assert public == {
        "channel": "homepage",
        "extraction_coverage": {"complete": True},
    }


def test_stale_dispatch_cannot_complete_reconciled_attempt() -> None:
    source_id = uuid.uuid4()
    current = {
        "in_flight": 1,
        "in_flight_source_id": str(source_id),
        "in_flight_dispatch_token": "attempt-2",
    }

    assert not run_dispatch_matches(
        current, source_id=source_id, dispatch_token="attempt-1"
    )
    assert run_dispatch_matches(
        current, source_id=source_id, dispatch_token="attempt-2"
    )


def test_cost_reservation_identity_tracks_durable_dispatch_epoch() -> None:
    common = {
        "source_id": uuid.uuid4(),
        "input_hash": "input-hash",
        "task_id": "celery-task",
        "retry": 0,
    }
    first = source_processing_reservation_id(dispatch_token="dispatch-1", **common)
    redelivery = source_processing_reservation_id(dispatch_token="dispatch-1", **common)
    reconciled = source_processing_reservation_id(dispatch_token="dispatch-2", **common)

    assert first == redelivery
    assert first != reconciled
    assert first != source_processing_reservation_id(
        dispatch_token="dispatch-1", **{**common, "retry": 1}
    )
