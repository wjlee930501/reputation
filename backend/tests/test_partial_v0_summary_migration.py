from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0048_normalize_partial_v0_summaries.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("partial_v0_summary_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_v0_summary_migration_is_scoped_to_usable_partial_runs(monkeypatch) -> None:
    migration = _load()
    statements: list[object] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.down_revision == "0047_add_incident_episode_seq"
    assert len(statements) == 1
    statement = statements[0]
    sql = " ".join(str(statement).split())
    assert "status = 'PARTIAL'" in sql
    assert "success_count > 0" in sql
    assert "error_summary->>'safe_error_code' IN" in sql
    assert statement.compile().params == {
        "safe_error_code": "V0_PARTIAL_PROVIDER_DEGRADED",
        "safe_error_message": "일부 AI 측정 호출은 실패했지만 성공 데이터로 초기 진단을 완료했습니다.",
        "next_action": (
            "초기 진단을 다시 실행하지 마세요. 운영센터에서 실패한 플랫폼의 상태만 확인하고 "
            "다음 정기 측정에서 회복 여부를 점검하세요."
        ),
    }
