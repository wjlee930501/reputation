"""Run selected hermetic tests without changing the host pytest collection."""
import os
import socket
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.environ.update({"REPUTATION_DISABLE_DOTENV": "1", "APP_ENV": "test",
                       "ADMIN_SECRET_KEY": "test-admin-key",
                       "OPENROUTER_API_KEY": "test-openrouter-key"})
    os.chdir(root / "backend")
    sys.path.insert(0, str(root / "backend"))

    def blocked(*args, **kwargs):
        raise OSError("Network disabled for isolated GEO hardening tests")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    import pytest

    return pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
