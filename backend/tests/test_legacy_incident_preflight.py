from __future__ import annotations

import json

from app.services.legacy_task_incident_inventory import LegacyIncidentInventory
from app.services.legacy_task_incident_reconciliation import LegacyIncidentReconciliation
from app.utils import legacy_publish_retirement_preflight as preflight
from app.utils import reconcile_legacy_task_incidents as reconcile_cli


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_preflight_blocks_convertible_and_reports_unknown(monkeypatch, capsys) -> None:
    monkeypatch.setattr(preflight.anyio, "run", lambda _function: (7, 0, 0))
    monkeypatch.setattr(preflight, "SyncSessionLocal", _Session)
    monkeypatch.setattr(
        preflight,
        "inspect_legacy_task_incidents",
        lambda _db: LegacyIncidentInventory(convertible_open=2, unknown_open=3),
    )

    assert preflight.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "BLOCKED",
        "total_historical": 7,
        "open_legacy_transport": 0,
        "unapplied_sent": 0,
        "convertible_legacy_incidents": 2,
        "unknown_legacy_incidents": 3,
    }


def test_preflight_allows_unknown_only_without_hiding_count(monkeypatch, capsys) -> None:
    monkeypatch.setattr(preflight.anyio, "run", lambda _function: (7, 0, 0))
    monkeypatch.setattr(preflight, "SyncSessionLocal", _Session)
    monkeypatch.setattr(
        preflight,
        "inspect_legacy_task_incidents",
        lambda _db: LegacyIncidentInventory(convertible_open=0, unknown_open=3),
    )

    assert preflight.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "READY"
    assert output["convertible_legacy_incidents"] == 0
    assert output["unknown_legacy_incidents"] == 3


def test_reconciliation_cli_prints_bounded_counts(monkeypatch, capsys) -> None:
    monkeypatch.setattr(reconcile_cli, "SyncSessionLocal", _Session)
    monkeypatch.setattr(
        reconcile_cli,
        "reconcile_legacy_task_incidents",
        lambda _db: LegacyIncidentReconciliation(
            converted=2,
            superseded=1,
            recovered=1,
            unknown=4,
        ),
    )

    assert reconcile_cli.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "APPLIED",
        "converted": 2,
        "superseded": 1,
        "recovered": 1,
        "unknown": 4,
    }
