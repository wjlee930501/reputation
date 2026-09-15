"""Run selected hermetic tests; never loads production dotenv or permits network."""
import os
import socket
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ.update({"REPUTATION_DISABLE_DOTENV": "1", "APP_ENV": "test",
                   "ADMIN_SECRET_KEY": "test-admin-key", "ANTHROPIC_API_KEY": "test-anthropic-key",
                   "OPENAI_API_KEY": "test-openai-key", "GEMINI_API_KEY": ""})
os.chdir(root / "backend")
sys.path.insert(0, str(root / "backend"))


def blocked(*args, **kwargs):
    raise OSError("Network disabled for isolated GEO hardening tests")


socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
import pytest  # noqa: E402

raise SystemExit(pytest.main(["-q", "--tb=short", "-p", "no:cacheprovider", *sys.argv[1:]]))
