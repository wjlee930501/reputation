from types import SimpleNamespace

from app.utils import backfill_content_images


def test_backfill_includes_legacy_images_without_policy_verification():
    sql = str(backfill_content_images._needs_image_backfill())

    assert "image_url IS NULL" in sql
    assert "image_policy_verified_at IS NULL" in sql


def test_backfill_requires_an_approved_philosophy(monkeypatch):
    monkeypatch.setattr(
        backfill_content_images,
        "get_current_approved_philosophy_sync",
        lambda *_args: None,
    )

    assert backfill_content_images._approved_image_direction(object(), SimpleNamespace(id="h")) is None


def test_backfill_builds_direction_from_the_approved_philosophy(monkeypatch):
    philosophy = SimpleNamespace(id="p")
    hospital = SimpleNamespace(id="h")
    expected = object()
    monkeypatch.setattr(
        backfill_content_images,
        "get_current_approved_philosophy_sync",
        lambda *_args: philosophy,
    )
    monkeypatch.setattr(
        backfill_content_images,
        "hospital_image_direction",
        lambda received_hospital, received_philosophy: (
            expected
            if (received_hospital, received_philosophy) == (hospital, philosophy)
            else None
        ),
    )

    assert backfill_content_images._approved_image_direction(object(), hospital) is expected
