from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core.celery_app import celery_app
from app.models.essence import PHOTO_SOURCE_TYPES, SourceType
from app.schemas.hospital import HospitalListItem
from app.services.essence_auto_review import AUTO_ESSENCE_ACTOR
from app.workers.tasks import AUTO_PUBLISH_ACTOR

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_photo_brand_is_a_supported_photo_source_type() -> None:
    assert SourceType.PHOTO_BRAND.value == "PHOTO_BRAND"
    assert SourceType.PHOTO_BRAND in PHOTO_SOURCE_TYPES


def test_hospital_response_keeps_domain_live_check_as_nullable_display_data() -> None:
    checked_at = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc).isoformat()
    payload = SimpleNamespace(
        id="hospital-id",
        name="테스트병원",
        slug="test-clinic",
        status="ACTIVE",
        plan=None,
        source_lead_id=None,
        profile_complete=True,
        v0_report_done=True,
        site_built=True,
        site_live=True,
        schedule_set=False,
        visual_approval_missing=[],
        aeo_domain="clinic.example.com",
        domain_cert_dns_verified_at=None,
        domain_cert_job_state=None,
        domain_last_checked_at=checked_at,
        domain_last_check_ok=False,
        domain_last_check_reason="TLS_PENDING",
        created_at=None,
    )

    serialized = HospitalListItem.model_validate(payload, from_attributes=True).model_dump()

    assert serialized["domain_last_checked_at"] == checked_at
    assert serialized["domain_last_check_ok"] is False
    assert serialized["domain_last_check_reason"] == "TLS_PENDING"
    # These observations are display fields; an ACTIVE hospital remains ACTIVE.
    assert serialized["status"] == "ACTIVE"
    assert serialized["schedule_set"] is False


def test_automatic_essence_approval_and_0800_publish_are_the_normal_runtime() -> None:
    publish_entry = celery_app.conf.beat_schedule["morning-content-auto-publish"]

    assert AUTO_ESSENCE_ACTOR == "SYSTEM_ESSENCE_AI_REVIEW"
    assert AUTO_PUBLISH_ACTOR == "SYSTEM_AUTO_PUBLISH"
    assert publish_entry["task"] == "app.workers.tasks.morning_content_auto_publish"
    assert publish_entry["schedule"].hour == {8}
    assert publish_entry["schedule"].minute == {0}


def test_flower_is_loopback_only_and_requires_explicit_credentials() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    flower = compose[compose.index("  flower:") :]

    assert '"127.0.0.1:5555:5555"' in flower
    assert "FLOWER_USER:?FLOWER_USER is required" in flower
    assert "FLOWER_PASSWORD:?FLOWER_PASSWORD is required" in flower
    assert "admin:changeme" not in flower
