"""Tier transition contract tests for the Starter/Grower/Leader rollout."""

import hashlib
from pathlib import Path

from app.core.config import Settings

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0039_update_content_plan_tiers.py"
)
MIGRATION_SHA256 = "bde3040110aeec0d59467b1c20f0aada0edd11a0ec7f522b19e6e4f9410fe908"


def test_tier_cost_guard_defaults_cover_fifty_leader_hospitals() -> None:
    """Given 50 Leader hospitals, when defaults load, then both generation guards allow 3,000 calls."""
    settings = Settings()

    assert settings.COST_GUARD_MONTHLY_CONTENT_CALLS == 3000
    assert settings.COST_GUARD_MONTHLY_IMAGE_CALLS == 3000


def test_plan_tier_migration_is_hash_pinned() -> None:
    """The intentionally lossy downgrade contract is documented and exercised by the native-Postgres test."""
    assert hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest() == MIGRATION_SHA256
