import logging

from app.core import observability


def test_configure_logging_suppresses_credential_bearing_http_transport_logs(monkeypatch):
    monkeypatch.setattr(observability, "_CONFIGURED", False)

    observability.configure_logging(level="INFO", json_logs=False)

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
