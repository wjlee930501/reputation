"""Structured logging, request-id propagation, and Sentry PII scrubbing.

Centralizes boot-time logging config (OBS-1), a request-id ContextVar + log filter
(OBS-2), and a Sentry ``before_send`` scrubber (OBS-4) so API and Celery emit
correlated, PII-safe logs.
"""
import json
import logging
import re
import sys
from contextvars import ContextVar

# Request-id correlation. Mirrors the actor ContextVar pattern in audit_log.py.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_CONFIGURED = False


def get_request_id() -> str:
    return request_id_ctx.get()


def set_request_id(value: str | None) -> str:
    request_id_ctx.set(value or "-")
    return request_id_ctx.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    """Cloud Logging-friendly JSON lines (``severity`` maps to log levels)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# 원장 사전 설문 링크는 주소 자체가 열쇠다(만료 없음). 섹션 설정 미리보기 링크도 10분 동안 열쇠다.
# 접근 로그에 토큰이 남지 않게 가린다.
_SURVEY_TOKEN_RE = re.compile(r"(/doctor-survey/)[A-Za-z0-9_-]{16,}")
_PREVIEW_TOKEN_RE = re.compile(r"(/site-preview/)[A-Za-z0-9_.-]{16,}")


def redact_link_tokens(value: object) -> object:
    if isinstance(value, str):
        if "/doctor-survey/" in value:
            value = _SURVEY_TOKEN_RE.sub(r"\1[token]", value)
        if "/site-preview/" in value:
            value = _PREVIEW_TOKEN_RE.sub(r"\1[token]", value)
    return value


class LinkTokenRedactionFilter(logging.Filter):
    """uvicorn 접근 로그 등에서 설문 링크 토큰을 지운다(인자와 메시지 모두)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_link_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_link_tokens(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact_link_tokens(arg) for key, arg in record.args.items()}
        return True


def _build_handler(json_logs: bool) -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.addFilter(LinkTokenRedactionFilter())
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] [req=%(request_id)s] %(message)s")
        )
    return handler


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Install a single stdout handler on the root + uvicorn loggers (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = _build_handler(json_logs)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own handlers; route them through ours for one format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False
    # httpx INFO records include the complete request URL. That is useful for
    # ordinary APIs but leaks credential-bearing URLs such as Slack incoming
    # webhooks and signed asset links into Cloud Logging. Application services
    # already log sanitized status/error details, so transport success logs are
    # deliberately suppressed in every API/Celery process.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _CONFIGURED = True


# ── PII scrubbing (OBS-4) ────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}")


def scrub_pii(text: str) -> str:
    if not text:
        return text
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    return text


def sentry_before_send(event: dict, hint: dict) -> dict:
    """Redact emails/phones from message + exception values before they leave the box."""
    try:
        if isinstance(event.get("message"), str):
            event["message"] = scrub_pii(event["message"])
        for exc in (event.get("exception", {}) or {}).get("values", []) or []:
            if isinstance(exc.get("value"), str):
                exc["value"] = scrub_pii(exc["value"])
        log_entry = event.get("logentry")
        if isinstance(log_entry, dict) and isinstance(log_entry.get("message"), str):
            log_entry["message"] = scrub_pii(log_entry["message"])
    except Exception:
        # Never let scrubbing crash event delivery.
        return event
    return event
