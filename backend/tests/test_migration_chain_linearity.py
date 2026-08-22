"""The alembic history must stay a single linear chain.

Production is stamped at `0054_add_hospital_content_customization` while main
only carried `0052_backfill_photo_asset_kind`. Recovering the missing hardening
revisions re-created the risk that two revisions claim the same parent, which
turns `alembic upgrade head` into a "Multiple head revisions" error and makes
`utils.production_readiness` (it calls `get_current_head()`) fail outright. These
tests pin the shape of the chain so the divergence cannot come back silently.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"

VISUAL_IDENTITY = "0051_add_hospital_visual_identity"
PHOTO_PROVENANCE = "0052_add_photo_asset_provenance"
IMAGE_POLICY = "0053_add_content_image_policy_verification"
CONTENT_CUSTOMIZATION = "0054_add_hospital_content_customization"
PHOTO_KIND_BACKFILL = "0055_backfill_photo_asset_kind"

PRODUCTION_STAMP = CONTENT_CUSTOMIZATION


def _script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return ScriptDirectory.from_config(config)


def test_history_has_exactly_one_head() -> None:
    script = _script_directory()

    assert script.get_heads() == [PHOTO_KIND_BACKFILL]
    assert script.get_current_head() == PHOTO_KIND_BACKFILL


def test_every_revision_has_at_most_one_parent_and_one_child() -> None:
    script = _script_directory()

    branched: list[tuple[str, frozenset[str]]] = []
    merged: list[tuple[str, object]] = []
    for revision in script.walk_revisions():
        if len(revision.nextrev) > 1:
            branched.append((revision.revision, revision.nextrev))
        if isinstance(revision.down_revision, (tuple, list)):
            merged.append((revision.revision, revision.down_revision))

    assert branched == []
    assert merged == []


def test_visual_identity_has_the_hardening_chain_as_its_only_child() -> None:
    script = _script_directory()

    visual_identity = script.get_revision(VISUAL_IDENTITY)

    assert visual_identity.nextrev == frozenset({PHOTO_PROVENANCE})


def test_hardening_revisions_keep_their_recovered_parents() -> None:
    script = _script_directory()

    recovered = (
        PHOTO_PROVENANCE,
        IMAGE_POLICY,
        CONTENT_CUSTOMIZATION,
        PHOTO_KIND_BACKFILL,
    )
    parents = {
        revision: script.get_revision(revision).down_revision for revision in recovered
    }

    assert parents == {
        PHOTO_PROVENANCE: VISUAL_IDENTITY,
        IMAGE_POLICY: PHOTO_PROVENANCE,
        CONTENT_CUSTOMIZATION: IMAGE_POLICY,
        PHOTO_KIND_BACKFILL: CONTENT_CUSTOMIZATION,
    }


def test_upgrade_from_the_production_stamp_runs_only_the_backfill() -> None:
    script = _script_directory()

    pending = [
        revision.revision for revision in script.iterate_revisions("heads", PRODUCTION_STAMP)
    ]

    assert pending == [PHOTO_KIND_BACKFILL]


def test_fresh_database_applies_the_whole_chain_in_order() -> None:
    script = _script_directory()

    applied = [
        revision.revision for revision in reversed(list(script.iterate_revisions("heads", "base")))
    ]

    assert len(applied) == len(set(applied))
    assert applied[-5:] == [
        VISUAL_IDENTITY,
        PHOTO_PROVENANCE,
        IMAGE_POLICY,
        CONTENT_CUSTOMIZATION,
        PHOTO_KIND_BACKFILL,
    ]
