"""이미지 생성 경로를 단계별로 짚어 어디서 끊겼는지 한 번에 말해 주는 진단기.

운영에서 "이미지가 안 만들어진다"는 증상 하나가 아홉 가지 원인에서 나온다 — 결제·할당량,
지역에 없는 모델, 워커 서비스 계정 권한, 버킷 이름 규칙, 비용 가드 상한, 정책 거절 반복.
콘텐츠 파이프라인은 이미지 실패를 본문 저장과 분리해 삼키도록 설계돼 있어서(이건 의도한
계약이다) 로그만으로는 원인을 고르기 어렵다.

이 모듈은 **운영 코드가 실제로 쓰는 함수와 설정 그대로** 각 단계를 한 번씩 밟고, 단계마다
PASS/FAIL과 공급자가 준 raw 오류를 그대로 찍는다. 마지막 줄에 처음 실패한 단계가 가리키는
"most likely cause"를 남긴다.

    cd backend && uv run python -m app.utils.check_image_provider

읽는 법과 단계별 조치는 `docs/ops/image-provider-runbook.md`를 본다.

주의: 4·5단계는 실제 유료 호출이다(이미지 1장 + 검수 1회). 비용 가드 카운터도 올라간다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
WARN = "WARN"

# 단계가 실패했을 때 사람이 할 일. 마지막 "most likely cause" 줄이 이걸 그대로 읽는다.
STAGE_REMEDIES: dict[str, str] = {
    "settings": (
        "OPENROUTER_API_KEY·GCP_STORAGE_BUCKET·IMAGE_PROVIDER를 Cloud Run 환경변수/Secret "
        "Manager에서 확인한다. 버킷 이름 규칙은 'reputation-images-<GCP_PROJECT_ID>'다."
    ),
    "openrouter_client": (
        "OPENROUTER_API_KEY가 유효한지, 워커 egress가 openrouter.ai에 닿는지 확인한다."
    ),
    "model_access": (
        "설정된 모델 슬러그가 OpenRouter 카탈로그에 없다 — GOOGLE_IMAGE_MODEL·"
        "OPENAI_IMAGE_MODEL·GEMINI_MODEL 값을 카탈로그의 실제 slug로 맞춘다."
    ),
    "image_generation": (
        "크레딧 부족·할당량(429), 모델 미제공(404), 요청 필드 거부(400)를 원문에서 구분한다. "
        "400이면 모델이 지원하는 파라미터(aspect_ratio/resolution/quality)를 의심한다."
    ),
    "policy_review": (
        "검수 모델(GEMINI_MODEL, 폴백 OPENAI_MODEL_PARSE)이 OpenRouter에 없거나 권한이 "
        "없으면 모든 이미지가 POLICY_UNAVAILABLE로 버려진다. 판정이 '거절'이면 정책 거절 "
        "루프이므로 프롬프트/주제를 본다."
    ),
    "gcs_upload": (
        "버킷이 없거나(이름 규칙), 서비스 계정에 storage.objects.create/delete 권한이 없다."
    ),
    "cost_guard": (
        "Redis에 닿지 않으면 가드는 fail-open이라 이미지 실패의 원인이 아니다. 오늘치 image/"
        "content 카운터가 상한에 닿았으면 COST_BLOCKED로 막힌 것이다."
    ),
    "provider_usage": "DB에 닿지 않으면 원장을 볼 수 없다. SYNC_DATABASE_URL을 확인한다.",
    "stored_failures": "DB에 닿지 않으면 저장된 실패 분류를 볼 수 없다.",
}


@dataclass
class StageResult:
    key: str
    title: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def remedy(self) -> str:
        return STAGE_REMEDIES.get(self.key, "")


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """비밀값은 존재 여부와 꼬리만 보여 준다. 진단 출력이 그대로 붙여넣어지기 때문이다."""

    text = (value or "").strip()
    if not text:
        return "(unset)"
    if len(text) <= keep:
        return "*" * len(text)
    return f"{'*' * (len(text) - keep)}{text[-keep:]}"


def format_error(error: BaseException) -> str:
    """공급자 오류는 가공하지 않는다 — 진단의 값은 원문에 있다."""

    parts = [f"{type(error).__name__}: {error}"]
    for attribute in ("status_code", "code", "reason"):
        value = getattr(error, attribute, None)
        if value is not None:
            parts.append(f"{attribute}={value}")
    return " | ".join(parts)


def most_likely_cause(results: list[StageResult]) -> str:
    """처음 실패한 단계가 원인이다. 뒤 단계의 실패는 대개 그 결과다."""

    for result in results:
        if result.status == FAIL:
            return f"{result.title} — {result.detail or 'failed'} → {result.remedy}"
    if any(result.status == WARN for result in results):
        warning = next(result for result in results if result.status == WARN)
        return f"{warning.title} — {warning.detail} → {warning.remedy}"
    skipped = [result.title for result in results if result.status == SKIP]
    if skipped:
        return (
            "실패한 단계가 없다. 건너뛴 단계가 있으니 같은 환경에서 다시 돌린다: "
            + ", ".join(skipped)
        )
    return "실패한 단계가 없다 — 이미지 경로는 이 환경에서 정상이다."


def render(results: list[StageResult]) -> str:
    lines = []
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}] {result.status:<4} {result.title}")
        if result.detail:
            lines.append(f"      {result.detail}")
        for key, value in result.data.items():
            lines.append(f"      - {key}: {value}")
        if result.status in (FAIL, WARN) and result.remedy:
            lines.append(f"      → {result.remedy}")
    lines.append("")
    lines.append(f"most likely cause: {most_likely_cause(results)}")
    return "\n".join(lines)


def _run(key: str, title: str, body: Callable[[], StageResult]) -> StageResult:
    try:
        return body()
    except Exception as error:  # noqa: BLE001 — 진단기는 어떤 오류에서도 계속 진행한다.
        return StageResult(key, title, FAIL, format_error(error))


# ── 단계 ────────────────────────────────────────────────────────────────


def stage_settings() -> StageResult:
    from app.core.config import settings

    data = {
        "IMAGE_PROVIDER": settings.IMAGE_PROVIDER,
        "IMAGE_FALLBACK_PROVIDER": settings.IMAGE_FALLBACK_PROVIDER or "(off)",
        "GOOGLE_IMAGE_MODEL": settings.GOOGLE_IMAGE_MODEL,
        "GEMINI_MODEL (policy reviewer)": settings.GEMINI_MODEL,
        "OPENAI_IMAGE_MODEL": settings.OPENAI_IMAGE_MODEL,
        "GCP_STORAGE_BUCKET": settings.GCP_STORAGE_BUCKET,
        "OPENROUTER_API_KEY": mask_secret(settings.OPENROUTER_API_KEY),
        "REDIS_URL": mask_secret(settings.REDIS_URL, keep=12),
    }
    expected_bucket = f"reputation-images-{settings.GCP_PROJECT_ID}"
    problems = []
    if not settings.OPENROUTER_API_KEY.strip():
        problems.append("OPENROUTER_API_KEY가 비어 있다 — 생성·검수 공급자가 없다")
    if settings.GCP_PROJECT_ID and settings.GCP_STORAGE_BUCKET != expected_bucket:
        problems.append(f"버킷 이름이 규칙과 다르다 (기대: {expected_bucket})")
    status = FAIL if problems else PASS
    return StageResult("settings", "settings/secrets resolved", status, "; ".join(problems), data)


def stage_openrouter_client() -> StageResult:
    from app.services import openrouter

    if not openrouter.configured():
        return StageResult(
            "openrouter_client", "OpenRouter client init", SKIP, "OPENROUTER_API_KEY가 비어 있다"
        )
    client = openrouter.sync_client(timeout=30.0)
    return StageResult(
        "openrouter_client",
        "OpenRouter client init",
        PASS,
        "",
        {"base_url": str(client.base_url)},
    )


def stage_model_access() -> StageResult:
    """설정된 슬러그가 OpenRouter 카탈로그에 실제로 있는지 확인한다(무과금 GET /models)."""

    import httpx

    from app.core.config import settings
    from app.services import openrouter

    if not openrouter.configured():
        return StageResult("model_access", "image model reachable", SKIP, "no OpenRouter key")
    response = httpx.get(
        f"{openrouter.OPENROUTER_BASE_URL}/models",
        headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
        timeout=30.0,
    )
    response.raise_for_status()
    catalog = {m.get("id") for m in response.json().get("data") or []}
    configured = {
        "GOOGLE_IMAGE_MODEL": settings.GOOGLE_IMAGE_MODEL,
        "OPENAI_IMAGE_MODEL": settings.OPENAI_IMAGE_MODEL,
        "GEMINI_MODEL": settings.GEMINI_MODEL,
        "OPENAI_MODEL_PARSE": settings.OPENAI_MODEL_PARSE,
    }
    missing = {name: slug for name, slug in configured.items() if slug not in catalog}
    if missing:
        return StageResult(
            "model_access",
            "image model reachable",
            FAIL,
            "카탈로그에 없는 모델 슬러그가 설정돼 있다",
            missing,
        )
    return StageResult(
        "model_access",
        "image model reachable",
        PASS,
        "",
        {name: slug for name, slug in configured.items()},
    )


def stage_generate_and_review() -> tuple[StageResult, StageResult, bytes | None]:
    """한 번의 생성 호출로 4단계(생성)와 5단계(정책 검수)를 함께 가른다.

    운영 함수 `_google_generate_verified_bytes`를 그대로 부른다 — 진단기가 별도 설정을
    복제하면 운영과 달라져, 진단이 통과하는데 운영은 막히는 상황이 생긴다.
    """

    from app.core.config import settings
    from app.models.content import ContentType
    from app.services import image_engine
    from app.services.image_policy import ImagePolicyRejectedError, ImagePolicyUnavailableError

    generation = StageResult("image_generation", "one image generation", SKIP)
    review = StageResult("policy_review", "policy review of that image", SKIP)
    if not settings.OPENROUTER_API_KEY.strip():
        generation.detail = review.detail = "no OpenRouter key"
        return generation, review, None

    topic = "여름철 탈수 예방 생활 수칙"
    prompt = image_engine._build_google_image_prompt(ContentType.HEALTH, topic)
    generation.data["prompt_head"] = prompt[:120] + "…"
    generation.data["model"] = settings.GOOGLE_IMAGE_MODEL
    try:
        image_bytes = image_engine._google_generate_verified_bytes(
            prompt, expected_topic=topic, counter=None
        )
    except ImagePolicyRejectedError as error:
        # 바이트는 나왔다 — 생성은 성공이고 검수가 거절했다.
        generation.status = PASS
        review.status = FAIL
        review.detail = "정책 검수가 거절했다"
        review.data = dict(error.assessment.model_dump())
        return generation, review, None
    except ImagePolicyUnavailableError as error:
        generation.status = PASS
        review.status = FAIL
        review.detail = format_error(error)
        review.data["reviewer_model"] = settings.GEMINI_MODEL
        return generation, review, None
    except Exception as error:  # noqa: BLE001
        generation.status = FAIL
        generation.detail = format_error(error)
        review.detail = "생성이 실패해 검수까지 가지 못했다"
        return generation, review, None
    generation.status = PASS
    generation.data["bytes"] = len(image_bytes)
    review.status = PASS
    return generation, review, image_bytes


def stage_gcs_roundtrip(image_bytes: bytes | None) -> StageResult:
    """설정된 버킷에 작은 객체를 쓰고 지운다 — 실제 발행에 쓰는 권한과 같은 권한이다."""

    import hashlib
    import uuid

    from app.core.config import settings
    from app.services.gcs_utils import _get_gcs_client

    payload = image_bytes or b"\x89PNG\r\n\x1a\n-diagnostic-"
    name = f"content/_diagnostic/{hashlib.sha256(payload).hexdigest()}-{uuid.uuid4().hex}.png"
    bucket = _get_gcs_client().bucket(settings.GCP_STORAGE_BUCKET)
    blob = bucket.blob(name)
    blob.upload_from_string(payload, content_type="image/png", if_generation_match=0)
    blob.delete()
    return StageResult(
        "gcs_upload",
        "GCS write+delete round-trip",
        PASS,
        "",
        {"bucket": settings.GCP_STORAGE_BUCKET, "object": name},
    )


def stage_cost_guard() -> StageResult:
    import asyncio

    from app.services import cost_guard

    snapshot = asyncio.run(cost_guard.get_usage_snapshot())
    return summarize_cost_guard(snapshot)


def summarize_cost_guard(snapshot: dict[str, Any]) -> StageResult:
    """스냅샷을 그대로 읽는다 — 관측 불가를 0건/정상으로 위장하지 않는다."""

    data: dict[str, Any] = {
        "availability": snapshot.get("availability"),
        "kill_switch_active": snapshot.get("kill_switch_active"),
        "enabled": snapshot.get("enabled"),
    }
    problems: list[str] = []
    if snapshot.get("availability") != "AVAILABLE":
        problems.append("Redis에 닿지 못해 오늘치 카운터를 읽지 못했다(가드는 fail-open)")
    if snapshot.get("kill_switch_active"):
        problems.append("비용 킬스위치가 켜져 있다 — 모든 카테고리가 차단된다")
    for usage in snapshot.get("categories") or []:
        category = usage.get("category")
        if category not in ("image", "content"):
            continue
        data[category] = json.dumps(usage, ensure_ascii=False, default=str)
        limit = usage.get("daily_limit") or 0
        used = usage.get("daily_used")
        if limit and used is not None and used >= limit:
            problems.append(f"{category} 일일 상한 도달({used}/{limit}) — COST_BLOCKED")
    status = WARN if problems else PASS
    return StageResult(
        "cost_guard", "cost guard reachable + today's counters", status, "; ".join(problems), data
    )


def stage_provider_usage(limit: int = 20) -> StageResult:
    from sqlalchemy import select

    from app.core.database import SyncSessionLocal
    from app.models.usage import ProviderUsageEvent

    rows: list[str] = []
    with SyncSessionLocal() as db:
        events = (
            db.execute(
                select(ProviderUsageEvent)
                .where(ProviderUsageEvent.workflow == "content_image_generation")
                .order_by(ProviderUsageEvent.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    for event in events:
        rows.append(
            f"{event.created_at:%Y-%m-%d %H:%M} {event.provider}/{event.model} "
            f"attempt={event.http_attempt} image_units={event.image_units} "
            f"usage_known={event.usage_known}"
        )
    detail = "" if rows else "최근 이미지 생성 호출 기록이 없다 — 공급자를 부르기 전에 막힌 것이다"
    return StageResult(
        "provider_usage",
        f"last {limit} image-generation provider attempts",
        PASS if rows else WARN,
        detail,
        {str(index): row for index, row in enumerate(rows, start=1)},
    )


def stage_stored_failures() -> StageResult:
    from sqlalchemy import text

    from app.core.database import SyncSessionLocal

    query = text(
        "SELECT essence_check_summary->'generation_attempt'->>'image_failure_class' AS failure_class, "
        "essence_check_summary->'generation_attempt'->>'reason' AS reason, COUNT(*) AS n "
        "FROM content_items "
        "WHERE essence_check_summary->'generation_attempt'->>'image_failure_class' IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY n DESC"
    )
    with SyncSessionLocal() as db:
        rows = db.execute(query).all()
    data = {f"{row.failure_class or '?'}/{row.reason or '?'}": row.n for row in rows}
    detail = "" if rows else "저장된 이미지 실패 분류가 없다"
    return StageResult(
        "stored_failures", "stored image failures by class", PASS, detail, data
    )


def run_all(*, skip_paid: bool = False, skip_db: bool = False) -> list[StageResult]:
    results = [
        _run("settings", "settings/secrets resolved", stage_settings),
        _run("openrouter_client", "OpenRouter client init", stage_openrouter_client),
        _run("model_access", "image model reachable", stage_model_access),
    ]
    image_bytes: bytes | None = None
    if skip_paid:
        results.append(StageResult("image_generation", "one image generation", SKIP, "--skip-paid"))
        results.append(StageResult("policy_review", "policy review of that image", SKIP, "--skip-paid"))
    else:
        generation, review, image_bytes = stage_generate_and_review()
        results.extend([generation, review])
    results.append(
        _run("gcs_upload", "GCS write+delete round-trip", lambda: stage_gcs_roundtrip(image_bytes))
    )
    results.append(_run("cost_guard", "cost guard reachable + today's counters", stage_cost_guard))
    if skip_db:
        results.append(StageResult("provider_usage", "provider attempts", SKIP, "--skip-db"))
        results.append(StageResult("stored_failures", "stored image failures", SKIP, "--skip-db"))
    else:
        results.append(
            _run("provider_usage", "last 20 image-generation provider attempts", stage_provider_usage)
        )
        results.append(
            _run("stored_failures", "stored image failures by class", stage_stored_failures)
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="이미지 생성 경로 단계별 진단")
    parser.add_argument(
        "--skip-paid", action="store_true", help="4·5단계(유료 생성·검수)를 건너뛴다"
    )
    parser.add_argument("--skip-db", action="store_true", help="8·9단계(DB 조회)를 건너뛴다")
    args = parser.parse_args(argv)
    results = run_all(skip_paid=args.skip_paid, skip_db=args.skip_db)
    print(render(results))
    return 1 if any(result.status == FAIL for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
