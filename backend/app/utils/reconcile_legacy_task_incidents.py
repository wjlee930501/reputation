"""Run one bounded legacy task-incident reconciliation batch.

Run with:
    python -m app.utils.reconcile_legacy_task_incidents
"""

from __future__ import annotations

import json

from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SyncSessionLocal
from app.services.legacy_task_incident_reconciliation import (
    reconcile_legacy_task_incidents,
)


def main() -> int:
    """Reconcile one batch and print count-only evidence."""

    try:
        with SyncSessionLocal() as db:
            result = reconcile_legacy_task_incidents(db)
    except (OSError, SQLAlchemyError) as error:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": "DATABASE_UNAVAILABLE",
                    "error_type": type(error).__name__,
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "APPLIED",
                "converted": result.converted,
                "superseded": result.superseded,
                "recovered": result.recovered,
                "unknown": result.unknown,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
