"""PDF 리포트 생성 엔진 — V0 및 월간 리포트"""
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import arrow
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.models.hospital import Hospital

logger = logging.getLogger(__name__)
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# 7가지 콘텐츠 유형 — 리포트 표 노출 순서(요금제 배분 순서와 동일).
CONTENT_TYPE_ORDER = ["FAQ", "DISEASE", "TREATMENT", "COLUMN", "HEALTH", "LOCAL", "NOTICE"]
ACTIVE_GAP_STATUSES = {"OPEN", "WATCHING"}
ACTIVE_ACTION_STATUSES = {"OPEN", "IN_PROGRESS", "BLOCKED"}
PRIORITY_RANK = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
PRIORITY_LABELS = {"HIGH": "높음", "NORMAL": "보통", "LOW": "낮음"}
SEVERITY_LABELS = {"CRITICAL": "심각", "HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음"}
GAP_TYPE_LABELS = {
    "NO_SUCCESSFUL_MEASUREMENT": "성공 측정값 없음",
    "MISSING_MENTION": "병원 미언급",
    "LOW_MENTION_SHARE": "낮은 AI 언급률",
    "COMPETITOR_VISIBILITY": "경쟁 병원 노출 우세",
    "SOURCE_SIGNAL_GAP": "AI 참고 근거 자료 부족",
}


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _content_type_counts(contents: list) -> dict[str, int]:
    """발행 콘텐츠를 7유형별 편수로 집계 (없는 유형은 0)."""
    counts = {t: 0 for t in CONTENT_TYPE_ORDER}
    for c in contents:
        key = str(_enum_value(getattr(c, "content_type", None)))
        if key in counts:
            counts[key] += 1
    return counts


def _query_text_of(record: Any) -> str | None:
    """SoV 레코드의 표시용 쿼리 텍스트 — QueryMatrix.query_text 우선, 없으면 타깃명."""
    query = getattr(record, "query", None)
    if query is not None and getattr(query, "query_text", None):
        return query.query_text
    target = getattr(record, "ai_query_target", None)
    if target is not None and getattr(target, "name", None):
        return target.name
    return None


def _mention_state(records: list) -> tuple[bool, bool]:
    """한 쿼리의 (측정됨, 언급됨) 상태 — calculate_sov와 동일한 성공 측정 필터 적용.

    - measurement_status == "FAILED" → 분모 제외
    - status 미존재 + raw_response 공백 → 네트워크 실패 추정, 제외
    성공 측정이 0건이면 (False, False) = '측정 없음'. 이 경우 전월 미측정으로 간주된다.
    """
    successful = []
    for r in records:
        status = getattr(r, "measurement_status", None)
        if status == "FAILED":
            continue
        raw = getattr(r, "raw_response", None)
        if status is None and raw is not None and not str(raw).strip():
            continue
        successful.append(r)
    if not successful:
        return (False, False)
    mentioned = any(getattr(r, "is_mentioned", False) for r in successful)
    return (True, mentioned)


def _related_content_titles(query_text: str, target_id: Any, contents: list) -> list[str]:
    """신규 언급 쿼리와 연관된 발행 콘텐츠 제목.

    1순위: exposure_content_linker가 설정한 content.query_target_id 링크 재사용.
    2순위: 링크가 없으면 쿼리 텍스트 토큰과 제목의 단순 키워드 매칭(폴백).
    """
    titles: list[str] = []
    seen: set[str] = set()

    if target_id is not None:
        for c in contents:
            title = getattr(c, "title", None)
            if getattr(c, "query_target_id", None) == target_id and title and title not in seen:
                titles.append(title)
                seen.add(title)
    if titles:
        return titles

    tokens = [t for t in re.split(r"\s+", query_text) if len(t) >= 2]
    for c in contents:
        title = getattr(c, "title", None)
        if not title or title in seen:
            continue
        if any(tok in title for tok in tokens):
            titles.append(title)
            seen.add(title)
    return titles


def build_content_attribution_summary(
    *,
    published_contents: list,
    prev_published_contents: list,
    this_records: list,
    prev_records: list,
    sov_pct: float | None,
    prev_sov_pct: float | None,
    change_pct: float | None,
    max_new_queries: int = 5,
) -> dict[str, Any]:
    """콘텐츠 발행과 AI 언급 변화를 상관 표기용으로 집계한다(인과 주장 아님).

    - 유형별 발행 편수(이번/전월)
    - 신규 언급 쿼리: 전월 미언급(또는 미측정) → 이번 달 언급 시작된 쿼리 최대 N개
    - 각 신규 언급 쿼리의 연관 발행 콘텐츠 제목(링크 우선, 키워드 폴백)
    반환 dict는 JSON 직렬화 가능(템플릿 렌더 + content_summary 저장 겸용).
    """
    this_by_query: dict[Any, list] = defaultdict(list)
    for r in this_records:
        this_by_query[getattr(r, "query_id", None)].append(r)
    prev_by_query: dict[Any, list] = defaultdict(list)
    for r in prev_records:
        prev_by_query[getattr(r, "query_id", None)].append(r)

    new_mention_queries: list[dict[str, Any]] = []
    for query_id, records in this_by_query.items():
        measured, mentioned = _mention_state(records)
        if not (measured and mentioned):
            continue
        # 전월에 이미 언급된 쿼리는 '신규'가 아니다. 전월 미측정도 신규로 인정.
        _, prev_mentioned = _mention_state(prev_by_query.get(query_id, []))
        if prev_mentioned:
            continue
        text = _query_text_of(records[0])
        if not text:
            continue
        target_id = next(
            (getattr(r, "ai_query_target_id", None) for r in records if getattr(r, "ai_query_target_id", None)),
            None,
        )
        new_mention_queries.append({
            "query_text": text,
            "related_contents": _related_content_titles(text, target_id, published_contents),
        })
        if len(new_mention_queries) >= max_new_queries:
            break

    return {
        "content_type_counts": _content_type_counts(published_contents),
        "prev_content_type_counts": _content_type_counts(prev_published_contents),
        "published_count": len(published_contents),
        "prev_published_count": len(prev_published_contents),
        "new_mention_queries": new_mention_queries,
        "new_mention_count": len(new_mention_queries),
        "sov_pct": sov_pct,
        "prev_sov_pct": prev_sov_pct,
        "change_pct": change_pct,
    }


def build_strategy_summary(
    *,
    query_targets: list,
    sov_records: list,
    exposure_gaps: list,
    exposure_actions: list,
    period_start: datetime,
    period_end: datetime,
    next_month: str,
) -> dict[str, Any]:
    """Build the durable monthly Query Target → evidence → action snapshot."""
    targets_by_id = {str(target.id): target for target in query_targets}
    query_to_target: dict[str, str] = {}
    for target in query_targets:
        for variant in getattr(target, "variants", None) or []:
            query_id = getattr(variant, "query_matrix_id", None)
            if query_id:
                query_to_target[str(query_id)] = str(target.id)

    records_by_target: dict[str, list] = defaultdict(list)
    for record in sov_records:
        target_id = getattr(record, "ai_query_target_id", None)
        target_key = str(target_id) if target_id else query_to_target.get(str(record.query_id))
        if target_key in targets_by_id:
            records_by_target[target_key].append(record)

    relevant_target_keys = {
        str(target.id)
        for target in query_targets
        if str(getattr(target, "status", "")).upper() == "ACTIVE"
    }
    relevant_target_keys.update(key for key, records in records_by_target.items() if records)
    relevant_target_keys.update(
        str(item.query_target_id)
        for item in [*exposure_gaps, *exposure_actions]
        if getattr(item, "query_target_id", None)
    )

    target_outcomes = []
    report_targets = [
        target for target in query_targets if str(target.id) in relevant_target_keys
    ]
    for target in sorted(report_targets, key=_strategy_target_sort_key):
        records = records_by_target.get(str(target.id), [])
        successful = [record for record in records if _successful_measurement(record)]
        platform_sov: dict[str, float | None] = {}
        platforms = sorted({str(record.ai_platform).lower() for record in records if record.ai_platform})
        for platform in platforms:
            platform_records = [
                record
                for record in successful
                if str(getattr(record, "ai_platform", "")).lower() == platform
            ]
            platform_sov[platform] = _record_sov(platform_records)
        target_outcomes.append({
            "id": str(target.id),
            "name": target.name,
            "priority": str(getattr(target, "priority", "NORMAL")).upper(),
            "priority_label": PRIORITY_LABELS.get(
                str(getattr(target, "priority", "NORMAL")).upper(),
                str(getattr(target, "priority", "NORMAL")),
            ),
            "platforms": list(getattr(target, "platforms", None) or []),
            "sov_pct": _record_sov(successful),
            "platform_sov": platform_sov,
            "successful_measurement_count": len(successful),
            "failed_measurement_count": len(records) - len(successful),
            "source_backed_count": sum(1 for record in successful if getattr(record, "source_urls", None)),
            "competitor_outcomes": _competitor_outcomes(successful),
            "last_measured_at": _iso_or_none(max(
                (getattr(record, "measured_at", None) for record in records),
                default=None,
            )),
        })

    gaps = [
        gap
        for gap in exposure_gaps
        if str(getattr(gap, "status", "")).upper() in ACTIVE_GAP_STATUSES
    ]
    gap_items = [
        {
            "id": str(gap.id),
            "query_target_id": str(gap.query_target_id) if gap.query_target_id else None,
            "query_target_name": _target_name(gap.query_target_id, targets_by_id),
            "gap_type": gap.gap_type,
            "gap_type_label": GAP_TYPE_LABELS.get(gap.gap_type, gap.gap_type),
            "severity": gap.severity,
            "severity_label": SEVERITY_LABELS.get(
                str(gap.severity).upper(), str(gap.severity)
            ),
            "status": gap.status,
            "evidence": gap.evidence or {},
        }
        for gap in sorted(gaps, key=_strategy_gap_sort_key)
    ]

    completed = [
        action
        for action in exposure_actions
        if str(getattr(action, "status", "")).upper() == "COMPLETED"
        and getattr(action, "completed_at", None) is not None
        and period_start <= action.completed_at <= period_end
    ]
    completed_items = [_serialize_strategy_action(action, targets_by_id) for action in sorted(
        completed,
        key=lambda action: getattr(action, "completed_at", period_start),
        reverse=True,
    )]

    active_actions = [
        action
        for action in exposure_actions
        if str(getattr(action, "status", "")).upper() in ACTIVE_ACTION_STATUSES
        and (
            getattr(action, "due_month", None) is None
            or str(action.due_month) <= next_month
        )
    ]
    next_actions = sorted(active_actions, key=_strategy_action_sort_key)[:3]

    return {
        "query_targets": target_outcomes,
        "exposure_gaps": gap_items,
        "completed_actions": completed_items,
        "next_month": next_month,
        "next_month_actions": [
            _serialize_strategy_action(action, targets_by_id) for action in next_actions
        ],
        "compliance_caveat": (
            "AI 답변 언급과 콘텐츠·근거 자료 변화는 같은 기간의 관찰 결과이며 인과관계를 "
            "단정하지 않습니다. 모든 실행안은 의료광고 관련 기준과 병원 내부 검수를 거쳐야 합니다."
        ),
    }


def _successful_measurement(record: Any) -> bool:
    status = getattr(record, "measurement_status", None)
    if str(status or "SUCCESS").upper() == "FAILED":
        return False
    if hasattr(record, "raw_response"):
        return bool(str(getattr(record, "raw_response", "") or "").strip())
    return True


def _competitor_outcomes(records: list) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        seen: set[str] = set()
        for item in getattr(record, "competitor_mentions", None) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen or not isinstance(item.get("is_mentioned"), bool):
                continue
            seen.add(name)
            values = counts.setdefault(name, {"observed_count": 0, "mention_count": 0})
            values["observed_count"] += 1
            if item["is_mentioned"]:
                values["mention_count"] += 1
    return [
        {
            "name": name,
            **values,
            "mention_pct": round(values["mention_count"] / values["observed_count"] * 100, 1),
        }
        for name, values in sorted(
            counts.items(),
            key=lambda pair: (-pair[1]["mention_count"], pair[0]),
        )
    ]


def _record_sov(records: list) -> float | None:
    if not records:
        return None
    return round(sum(1 for record in records if getattr(record, "is_mentioned", False)) / len(records) * 100, 1)


def _strategy_target_sort_key(target: Any) -> tuple[int, str]:
    priority = str(getattr(target, "priority", "NORMAL")).upper()
    return (PRIORITY_RANK.get(priority, 9), str(getattr(target, "name", "")))


def _strategy_gap_sort_key(gap: Any) -> tuple[int, str, str]:
    severity = str(getattr(gap, "severity", "MEDIUM")).upper()
    return (
        SEVERITY_RANK.get(severity, 9),
        str(getattr(gap, "gap_type", "")),
        str(getattr(gap, "id", "")),
    )


def _strategy_action_sort_key(action: Any) -> tuple[int, int, str, str]:
    target = getattr(action, "query_target", None)
    gap = getattr(action, "gap", None)
    priority = str(getattr(target, "priority", "NORMAL")).upper()
    severity = str(getattr(gap, "severity", "MEDIUM")).upper()
    return (
        PRIORITY_RANK.get(priority, 9),
        SEVERITY_RANK.get(severity, 9),
        str(getattr(action, "due_month", None) or "9999-99"),
        str(getattr(action, "title", "")),
    )


def _serialize_strategy_action(action: Any, targets_by_id: dict[str, Any]) -> dict[str, Any]:
    linked_content = getattr(action, "linked_content", None)
    gap = getattr(action, "gap", None)
    return {
        "id": str(action.id),
        "query_target_id": str(action.query_target_id) if action.query_target_id else None,
        "query_target_name": _target_name(action.query_target_id, targets_by_id),
        "gap_type": getattr(gap, "gap_type", None),
        "severity": getattr(gap, "severity", None),
        "action_type": action.action_type,
        "title": action.title,
        "description": action.description,
        "owner": action.owner,
        "due_month": action.due_month,
        "status": action.status,
        "completed_at": _iso_or_none(getattr(action, "completed_at", None)),
        "linked_content_id": str(linked_content.id) if linked_content else None,
        "linked_content_title": getattr(linked_content, "title", None),
    }


def _target_name(target_id: Any, targets_by_id: dict[str, Any]) -> str | None:
    target = targets_by_id.get(str(target_id)) if target_id else None
    return getattr(target, "name", None)


def _iso_or_none(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else str(value) if value else None


def generate_pdf_report(
    hospital: Hospital,
    period_start: datetime,
    period_end: datetime,
    report_type: str = "MONTHLY",
    sov_pct: float | None = 0.0,
    published_count: int = 0,
    repeat_count: int = 5,
    attribution: dict[str, Any] | None = None,
    strategy: dict[str, Any] | None = None,
) -> str:
    """
    PDF 리포트 생성 후 GCS에 업로드.
    Returns: gs://reputation-reports/reports/{slug}/{filename} 경로
    """
    from weasyprint import HTML

    output_dir = Path(settings.REPORT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = arrow.now("Asia/Seoul")
    label = "V0-진단" if report_type == "V0" else arrow.get(period_start).format("YYYY-MM")
    filename = f"{hospital.slug}_{label}.pdf"
    local_pdf_path = output_dir / filename

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    template = env.get_template("report.html")
    html = template.render(
        hospital=hospital,
        report_type=report_type,
        period_label=label,
        period_start=period_start,
        period_end=period_end,
        sov_pct=sov_pct,
        # sov_pct is None → '측정 데이터 없음'. 템플릿이 None과 실제 0.0을 구분해 렌더링한다.
        sov_measured=sov_pct is not None,
        published_count=published_count,
        # 각 쿼리를 실제 몇 회 반복 발송했는지 — 하드코딩(10회) 대신 호출부 값 전달.
        repeat_count=repeat_count,
        # 콘텐츠 발행-AI 언급 상관 섹션 데이터(월간 전용). None이면 섹션 미노출.
        attribution=attribution,
        strategy=strategy,
        generated_at=now.datetime,
    )

    HTML(string=html).write_pdf(str(local_pdf_path))
    logger.info(f"PDF generated: {local_pdf_path}")

    # GCS 업로드
    gcs_path = _upload_to_gcs(local_pdf_path, hospital.slug, filename)

    # GCS 업로드 성공 시에만 로컬 파일 삭제
    if gcs_path.startswith("gs://"):
        try:
            local_pdf_path.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete local PDF {local_pdf_path}: {e}")

    return gcs_path


def _upload_to_gcs(local_path: Path, slug: str, filename: str) -> str:
    """PDF를 GCS에 업로드하고 gs:// 경로를 반환한다."""
    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(settings.GCS_REPORTS_BUCKET)
        blob_name = f"reports/{slug}/{filename}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path), content_type="application/pdf")
        gcs_path = f"gs://{settings.GCS_REPORTS_BUCKET}/{blob_name}"
        logger.info(f"PDF uploaded to GCS: {gcs_path}")
        return gcs_path
    except Exception as e:
        logger.error(f"GCS upload failed: {e}")
        if settings.APP_ENV == "production":
            raise RuntimeError(f"GCS upload failed in production: {e}") from e
        return str(local_path)
