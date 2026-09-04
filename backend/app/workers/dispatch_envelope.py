"""Build release-bound, argument-bound Celery dispatch envelopes."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.config import settings

DISPATCH_TTL_SECONDS = 3600
CLOCK_SKEW_SECONDS = 30
PREFIX = "reputation_dispatch_"
PURPOSE_HEADER = f"{PREFIX}purpose"
TARGET_HEADER = f"{PREFIX}target"
TASK_ID_HEADER = f"{PREFIX}task_id"
RETRIES_HEADER = f"{PREFIX}retries"
ISSUED_HEADER = f"{PREFIX}issued_at"
EXPIRES_HEADER = f"{PREFIX}expires_at"
RELEASE_HEADER = f"{PREFIX}release"
ARGS_DIGEST_HEADER = f"{PREFIX}args_digest"
OPERATION_RUN_HEADER = f"{PREFIX}operation_run_id"
SIGNATURE_HEADER = f"{PREFIX}signature"
GLOBAL_TARGET = "*"

TASK_PURPOSES = {
    "app.workers.tasks.trigger_v0_report": "trigger-v0-report",
    "app.workers.tasks.build_aeo_site": "build-aeo-site",
    "app.workers.tasks.nightly_content_generation": "nightly-content-generation",
    "app.workers.tasks.overnight_content_generation_recovery": (
        "overnight-content-generation-recovery"
    ),
    "app.workers.tasks.prepublish_content_generation_recovery": (
        "prepublish-content-generation-recovery"
    ),
    "app.workers.tasks.regenerate_content_item": "regenerate-content",
    "app.workers.tasks.auto_review_essence_snapshot": "auto-review-essence-snapshot",
    "app.workers.tasks.reconcile_essence_snapshots": "reconcile-essence-snapshots",
    "app.workers.tasks.generate_content_image": "generate-content-image",
    "app.workers.tasks.morning_content_auto_publish": "morning-content-auto-publish",
    "app.workers.tasks.run_sov_for_hospital": "run-sov",
    "app.workers.tasks.monthly_slot_generation": "monthly-slot-generation",
    "app.workers.tasks.run_weekly_monitoring": "weekly-sov-monitoring",
    "app.workers.tasks.run_monthly_sov_measurement": "monthly-sov-measurement",
    "app.workers.tasks.adjust_query_priorities": "adjust-query-priorities",
    "app.workers.tasks.run_monthly_reports": "monthly-reports",
    "app.workers.tasks.summarize_monthly_report_gaps": "monthly-report-gap-summary",
    "app.workers.tasks.retry_site_revalidation": "retry-site-revalidation",
    "app.workers.tasks.monitor_live_custom_domains": "live-custom-domain-health",
    "app.workers.tasks.purge_expired_leads": "purge-expired-leads",
    "app.workers.naver_sync.weekly_naver_source_sync": "weekly-naver-source-sync",
    "app.workers.lead_diagnosis_tasks.drain_lead_diagnoses": "drain-lead-diagnoses",
    "app.workers.autonomous_recovery.reconcile": "reconcile-autonomous-workflows",
    "app.workers.content_backlog_recovery.reconcile": "reconcile-stranded-content",
    "app.workers.domain_certificate_tasks.provision_domain_certificate": (
        "provision-domain-certificate"
    ),
    "app.workers.canary_tasks.canary_default": "canary-default",
    "app.workers.canary_tasks.canary_content": "canary-content",
    "app.workers.canary_tasks.canary_sov": "canary-sov",
    "app.workers.canary_tasks.canary_reports": "canary-reports",
    "app.workers.canary_tasks.canary_leadgen": "canary-leadgen",
    "app.workers.canary_tasks.canary_certificates": "canary-certificates",
}

FIRST_ARG_TARGET_TASKS = frozenset(
    {
        "app.workers.tasks.process_source_asset_task",
        "app.workers.tasks.trigger_v0_report",
        "app.workers.tasks.build_aeo_site",
        "app.workers.tasks.regenerate_content_item",
        "app.workers.tasks.auto_review_essence_snapshot",
        "app.workers.tasks.generate_content_image",
        "app.workers.tasks.run_sov_for_hospital",
        "app.workers.tasks.generate_monthly_report_for_hospital",
        "app.workers.tasks.retry_site_revalidation",
        "app.workers.domain_certificate_tasks.provision_domain_certificate",
        "app.workers.lead_diagnosis_tasks.notify_lead_intake",
        "app.workers.lead_diagnosis_tasks.run_lead_diagnosis",
        "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_measurement",
        "app.workers.lead_diagnosis_tasks.build_lead_report",
        "app.workers.lead_diagnosis_tasks.recover_lead_diagnosis_report",
        "app.workers.lead_diagnosis_tasks.send_lead_report_email",
    }
)


def is_protected_task(task_name: str) -> bool:
    return task_name.startswith("app.workers.")


def build_dispatch_headers(purpose: str, target_id: str | None = None) -> dict[str, str]:
    """Attach intent; ``before_task_publish`` adds the bounded message signature."""
    return {PURPOSE_HEADER: purpose, TARGET_HEADER: target_id or GLOBAL_TARGET}


def stamp_dispatch_headers(
    *,
    task_name: str,
    task_id: str,
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    retries: int,
    headers: Mapping[str, Any] | None,
    now: int | None = None,
) -> dict[str, Any]:
    stamped = dict(headers or {})
    if not is_protected_task(task_name):
        return stamped
    issued_at = int(time.time() if now is None else now)
    values = {
        PURPOSE_HEADER: expected_purpose(task_name),
        TARGET_HEADER: expected_target(task_name, args),
        TASK_ID_HEADER: task_id,
        RETRIES_HEADER: str(retries),
        ISSUED_HEADER: str(issued_at),
        EXPIRES_HEADER: str(issued_at + DISPATCH_TTL_SECONDS),
        RELEASE_HEADER: release_revision(),
        ARGS_DIGEST_HEADER: args_digest(args, kwargs),
        OPERATION_RUN_HEADER: str(stamped.get("operation_run_id") or "-"),
    }
    stamped.update(values)
    stamped[SIGNATURE_HEADER] = signature(task_name, values)
    return stamped


def stamp_published_message(
    sender: str | None = None,
    body: Any = None,
    headers: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> None:
    if not sender or headers is None or not is_protected_task(sender):
        return
    args, kwargs = message_args(body)
    headers.update(
        stamp_dispatch_headers(
            task_name=sender,
            task_id=str(headers.get("id") or ""),
            args=args,
            kwargs=kwargs,
            retries=int(headers.get("retries") or 0),
            headers=headers,
        )
    )


def expected_purpose(task_name: str) -> str:
    return TASK_PURPOSES.get(task_name, task_name)


def expected_target(task_name: str, args: Sequence[Any]) -> str:
    if task_name in FIRST_ARG_TARGET_TASKS and args:
        return str(args[0])
    return GLOBAL_TARGET


def args_digest(args: Sequence[Any], kwargs: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"args": list(args), "kwargs": dict(kwargs)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def signature(task_name: str, values: Mapping[str, str]) -> str:
    fields = ["reputation-celery-v2", task_name]
    fields.extend(values[key] for key in signed_header_names(include_signature=False))
    return hmac.new(
        settings.WORKER_DISPATCH_SECRET.encode(), "\n".join(fields).encode(), hashlib.sha256
    ).hexdigest()


def signed_header_names(*, include_signature: bool = True) -> tuple[str, ...]:
    names = (
        PURPOSE_HEADER,
        TARGET_HEADER,
        TASK_ID_HEADER,
        RETRIES_HEADER,
        ISSUED_HEADER,
        EXPIRES_HEADER,
        RELEASE_HEADER,
        ARGS_DIGEST_HEADER,
        OPERATION_RUN_HEADER,
    )
    return (*names, SIGNATURE_HEADER) if include_signature else names


def release_revision() -> str:
    revision = settings.REPUTATION_RELEASE_REVISION.strip()
    if settings.APP_ENV.lower() == "production" and not revision:
        from app.workers.dispatch_auth import DispatchAuthorizationError

        raise DispatchAuthorizationError("missing worker release revision")
    return revision or "development"


def message_args(body: Any) -> tuple[Sequence[Any], Mapping[str, Any]]:
    if isinstance(body, (list, tuple)) and len(body) >= 2:
        args = body[0] if isinstance(body[0], (list, tuple)) else []
        kwargs = body[1] if isinstance(body[1], Mapping) else {}
        return args, kwargs
    return [], {}
