# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: loaded only by the isolated image's explicit PYTHONPATH.
"""Test-only transport address adapter. No task, gate, or certification patches."""
import os

if os.environ.get("REHEARSAL_BOUNDARIES") == "1":
    from app.services import openrouter

    openrouter.OPENROUTER_BASE_URL = "http://fixture:8090/api/v1"
