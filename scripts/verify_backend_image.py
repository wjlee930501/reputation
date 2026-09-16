"""Offline smoke of the non-root production backend image, without dev dependencies.

Run via Docker with --network none and explicit test-only environment values.
No DB connection, task dispatch, provider call, or production mutation is made.
Importing this module has no verification or environment side effects.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import platform
from pathlib import Path
from typing import Any

SERVICE_MODULES = (
    "content_generation_review",
    "content_review_feedback",
    "v0_measurement_snapshot",
    "measurement_selection",
    "measurement_manifest_policy",
    "monthly_publication_facts",
)


def require_isolated_image() -> None:
    """Fail before application imports when used outside the intended test image."""
    if (
        os.environ.get("APP_ENV") != "test"
        or os.environ.get("REPUTATION_DISABLE_DOTENV") != "1"
    ):
        raise RuntimeError("Explicit test environment and disabled dotenv are required")
    if platform.system() != "Linux" or os.geteuid() == 0:
        raise RuntimeError("A non-root Linux runtime image is required")
    if Path.cwd() != Path("/app"):
        raise RuntimeError("Run from the production image working directory")


async def verify_liveness() -> dict[str, bool]:
    """Exercise the ASGI app and middleware without opening a network socket."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    checks: dict[str, bool] = {}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://image.test"
    ) as client:
        for route in ("/health/live", "/api/v1/health/live"):
            response = await client.get(route)
            checks[route] = response.status_code == 200 and response.json() == {
                "status": "ok"
            }
        response = await client.get("/docs")
        checks["development_docs_disabled"] = response.status_code == 404
    return checks


def verify_application() -> dict[str, Any]:
    """Import the shipped services before the task registry to expose import cycles."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    for name in SERVICE_MODULES:
        module = importlib.import_module(f"app.services.{name}")
        if (
            not Path(module.__file__)
            .resolve()
            .is_relative_to(Path("/app/app/services"))
        ):
            raise RuntimeError(
                "Service was not imported from the production source bundle"
            )
    from app.utils.production_readiness import _workflow_facts

    # Importing WeasyPrint loads its native text/FFI libraries in the final stage.
    importlib.import_module("weasyprint")
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    checks = {
        **_workflow_facts(),
        **asyncio.run(verify_liveness()),
        "single_migration_head": len(heads) == 1,
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "schema_heads": heads,
        "service_modules": len(SERVICE_MODULES),
        "scope": "OFFLINE_IMAGE_SMOKE_NOT_PRODUCTION_READINESS",
    }


def main() -> int:
    try:
        require_isolated_image()
        report = verify_application()
    except Exception as exc:
        report = {"ready": False, "error_type": type(exc).__name__}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
