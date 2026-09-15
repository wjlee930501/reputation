"""Validate the locally owned, disposable release-verification environment."""
import json
import os
from pathlib import Path


def load_context():
    manifest = Path(os.environ.get(
        "REPUTATION_RELEASE_MANIFEST", "/tmp/reputation-approval-current.json"
    ))
    data = json.loads(manifest.read_text())
    root = Path(data["root"]).resolve()
    if not str(root).startswith("/private/tmp/reputation-approval-"):
        raise RuntimeError("A dedicated macOS temporary verification directory is required")
    if root.stat().st_uid != os.getuid():
        raise RuntimeError("Verification directory must belong to the current user")
    ports = [data[k] for k in ("pg_port", "redis_port", "admin_port", "site_port", "api_port")]
    if len(set(ports)) != 5 or any(type(p) is not int or not 49152 <= p <= 65535 for p in ports):
        raise RuntimeError("Verification requires five distinct high loopback ports")
    pid = (root / "pgdata/postmaster.pid").read_text().splitlines()
    if Path(pid[1]).resolve() != root / "pgdata" or int(pid[3]) != data["pg_port"]:
        raise RuntimeError("PostgreSQL identity does not match the disposable manifest")
    os.kill(int(pid[0]), 0)
    data["root"] = str(root)
    return data
