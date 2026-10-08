"""Read-only deployment gate for retiring normal publication notifications.

Run with:
    python -m app.utils.legacy_publish_retirement_preflight
"""

from __future__ import annotations

import json

import anyio
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SyncSessionLocal, get_async_sessionmaker
from app.services.content_publish_reconciliation import inspect_legacy_publish_history
from app.services.legacy_task_incident_inventory import (
    inspect_legacy_task_incidents,
)


async def _inspect() -> tuple[int, int, int]:
    inventory = await inspect_legacy_publish_history(get_async_sessionmaker())
    return inventory.total, inventory.open_transport, inventory.unapplied_sent


def main() -> int:
    """Print count-only evidence and fail closed on backlog or unavailable DB."""

    try:
        total, open_transport, unapplied_sent = anyio.run(_inspect)
        with SyncSessionLocal() as db:
            legacy_incidents = inspect_legacy_task_incidents(db)
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
    retirable = (
        open_transport == 0
        and unapplied_sent == 0
        and legacy_incidents.convertible_open == 0
    )
    print(
        json.dumps(
            {
                "status": "READY" if retirable else "BLOCKED",
                "total_historical": total,
                "open_legacy_transport": open_transport,
                "unapplied_sent": unapplied_sent,
                "convertible_legacy_incidents": legacy_incidents.convertible_open,
                "unknown_legacy_incidents": legacy_incidents.unknown_open,
            },
            sort_keys=True,
        )
    )
    return 0 if retirable else 1


if __name__ == "__main__":
    raise SystemExit(main())
