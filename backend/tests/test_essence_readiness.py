import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.essence import SourceStatus, SourceType
from app.services.essence_engine import compute_sources_snapshot_hash
from app.services.essence_readiness import resolve_essence_readiness


def _source(*, status=SourceStatus.PROCESSED, source_type=SourceType.HOMEPAGE):
    return SimpleNamespace(
        id=uuid.uuid4(),
        content_hash="hash",
        status=status,
        source_type=source_type,
        processed_at=datetime.now(timezone.utc) if status == SourceStatus.PROCESSED else None,
    )


def test_current_approved_requires_exact_complete_source_snapshot():
    source = _source()
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([source]))
    readiness = resolve_essence_readiness(philosophy, [source])
    assert readiness.current is philosophy
    assert readiness.is_fresh is True


def test_new_pending_text_source_immediately_makes_approval_stale():
    processed = _source()
    pending = _source(status=SourceStatus.PENDING)
    philosophy = SimpleNamespace(source_snapshot_hash=compute_sources_snapshot_hash([processed]))
    readiness = resolve_essence_readiness(philosophy, [processed, pending])
    assert readiness.current is None
    assert readiness.is_stale is True
    assert readiness.has_unprocessed_sources is True
