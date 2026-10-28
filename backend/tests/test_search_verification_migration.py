"""0093 seeds daonneuro through the same hospital columns the product edits."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0093_add_hospital_search_verification.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0093", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0093_seeds_exact_daonneuro_ownership_values():
    migration = _module()

    assert migration._SLUG == "sejongdaonsingyeonggwayiweon"
    assert migration._NAVER_TOKEN == "bb6f7e8f49eb178c06769cb81c16174c8a07a69a"
    assert migration._DAUM_PHRASE == (
        "#DaumWebMasterTool:"
        "ec0bfd6a69d68c57e1f24bfe8b935880a1f5d4d1cecb8c30e23ffab73ddb9c47:"
        "WoYFtMaR84eZ7MaX1Xjw9A=="
    )
