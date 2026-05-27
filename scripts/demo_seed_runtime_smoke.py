#!/usr/bin/env python3
"""Runtime smoke test for the deterministic Re:putation demo seed.

This script is intentionally narrow and read-mostly after reseeding the demo
hospital. It verifies the core AI exposure/content-operations path without
calling external LLM providers:

1. backend health
2. deterministic demo seed
3. Admin hospital/source/philosophy/content/report contracts
4. Public hospital/content exposure policy

It never prints secrets. ADMIN_SECRET_KEY is loaded from the environment or the
repo .env file and is only used as an HTTP header.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("REPUTATION_BASE_URL", "http://localhost:8000").rstrip("/")
DEMO_SLUG = os.environ.get("REPUTATION_DEMO_SLUG", "motionlabs-orthopedics-demo")
MIN_PUBLIC_CONTENTS = 10


class SmokeFailure(RuntimeError):
    pass


def load_admin_key() -> str:
    if os.environ.get("ADMIN_SECRET_KEY"):
        return os.environ["ADMIN_SECRET_KEY"]
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("ADMIN_SECRET_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SmokeFailure("ADMIN_SECRET_KEY is missing. Set it in env or repo .env.")


ADMIN_KEY = load_admin_key()


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SmokeFailure(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout.strip()


def request_json(path: str, *, admin: bool = False) -> Any:
    headers = {"Accept": "application/json"}
    if admin:
        headers["X-Admin-Key"] = ADMIN_KEY
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"HTTP {exc.code} for {path}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise SmokeFailure(f"HTTP request failed for {path}: {exc}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)
    print(f"✓ {message}")


def find_demo_hospital(hospitals: list[dict[str, Any]]) -> dict[str, Any]:
    for hospital in hospitals:
        if hospital.get("slug") == DEMO_SLUG:
            return hospital
    raise SmokeFailure(f"Demo hospital not found in admin list: {DEMO_SLUG}")


def main() -> int:
    health = request_json("/health")
    require(health == {"status": "ok"}, "backend health OK")

    seed_output = run([
        "docker",
        "compose",
        "exec",
        "-T",
        "api",
        "python",
        "-m",
        "app.utils.demo_seed",
        "--generate",
        "--publish",
    ])
    require(DEMO_SLUG in seed_output, "demo seed completed")

    hospitals = request_json("/api/v1/admin/hospitals", admin=True)
    require(isinstance(hospitals, list), "admin hospital list returned")
    hospital = find_demo_hospital(hospitals)
    hospital_id = hospital["id"]
    require(hospital.get("status") == "ACTIVE", "demo hospital is ACTIVE")

    sources = request_json(f"/api/v1/admin/hospitals/{hospital_id}/essence/sources", admin=True)
    require(isinstance(sources, list) and len(sources) >= 1, "source asset exists")
    require(any(s.get("status") == "PROCESSED" and s.get("evidence_note_count", 0) > 0 for s in sources), "processed source has evidence notes")

    philosophies = request_json(f"/api/v1/admin/hospitals/{hospital_id}/essence/philosophies", admin=True)
    require(isinstance(philosophies, list) and any(p.get("status") == "APPROVED" for p in philosophies), "approved operating standard exists")

    contents = request_json(f"/api/v1/admin/hospitals/{hospital_id}/content", admin=True)
    require(isinstance(contents, list) and len(contents) >= 16, "admin content slots exist")
    aligned_published = [
        c for c in contents
        if c.get("status") == "PUBLISHED" and c.get("essence_status") == "ALIGNED" and c.get("content_philosophy_id")
    ]
    require(
        len(aligned_published) >= MIN_PUBLIC_CONTENTS,
        f"at least {MIN_PUBLIC_CONTENTS} published contents are aligned to approved operating standard",
    )

    reports = request_json(f"/api/v1/admin/hospitals/{hospital_id}/reports", admin=True)
    require(isinstance(reports, list) and len(reports) >= 1, "admin report exists")
    report = reports[0]
    report_detail = request_json(f"/api/v1/admin/hospitals/{hospital_id}/reports/{report['id']}", admin=True)
    essence_summary = report_detail.get("essence_summary") or {}
    require(bool(essence_summary.get("approved_philosophy_exists")), "report includes approved operating-standard summary")
    require((essence_summary.get("source_count") or 0) >= 1, "report includes reviewed source count")
    require(
        (essence_summary.get("aligned_content_count") or 0) >= MIN_PUBLIC_CONTENTS,
        f"report includes at least {MIN_PUBLIC_CONTENTS} aligned contents",
    )

    public_hospital = request_json(f"/api/v1/public/hospitals/{DEMO_SLUG}")
    require(public_hospital.get("director_philosophy") is None, "public profile does not expose legacy director philosophy")

    public_contents = request_json(f"/api/v1/public/hospitals/{DEMO_SLUG}/contents")
    require(
        isinstance(public_contents, list) and len(public_contents) == len(aligned_published),
        "public contents expose only aligned published items",
    )
    require(
        len(public_contents) >= MIN_PUBLIC_CONTENTS,
        f"public site exposes at least {MIN_PUBLIC_CONTENTS} demo content cases",
    )
    content_id = public_contents[0]["id"]
    public_detail = request_json(f"/api/v1/public/hospitals/{DEMO_SLUG}/contents/{content_id}")
    require("body" in public_detail and "모션랩스정형외과의원" in public_detail["body"], "public content detail is readable")

    print("\nDemo seed runtime smoke passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"✗ {exc}", file=sys.stderr)
        raise SystemExit(1)
