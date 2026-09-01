"""PDF 리포트 생성 엔진 — V0 및 월간 리포트"""
import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import arrow
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.models.hospital import Hospital
from app.services import sov_engine
from app.services.content_citations import platform_owned_source_roots
from app.services.doctor_pdf_contracts import (
    DoctorAppendixRow,
    DoctorEvidence,
    DoctorEvidenceCase,
    DoctorMentionSentence,
    DoctorNextActions,
    DoctorPublishedItem,
    DoctorReportView,
    DoctorTile,
    DoctorV0Baseline,
)
from app.services.monthly_sov_types import MonthlySovPayload
from app.services.report_attribution import (
    CitationSummaryPayload,
    ContentAttributionPayload,
    QuestionRowPayload,
)
from app.services.sov_statistics import DeltaSignificance
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# 7가지 콘텐츠 유형 — 리포트 표 노출 순서(요금제 배분 순서와 동일).
ACTIVE_GAP_STATUSES = {"OPEN", "WATCHING"}
ACTIVE_ACTION_STATUSES = {"OPEN", "IN_PROGRESS", "BLOCKED"}
PRIORITY_RANK = {"HIGH": 0, "NORMAL": 1, "LOW": 2}
SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
PRIORITY_LABELS = {"HIGH": "높음", "NORMAL": "보통", "LOW": "낮음"}
SEVERITY_LABELS = {"CRITICAL": "심각", "HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음"}
GAP_TYPE_LABELS = {
    "NO_SUCCESSFUL_MEASUREMENT": "성공 측정값 없음",
    "TARGET_NOT_MEASURED": "아직 측정 안 된 질문",
    "MISSING_MENTION": "병원 미언급",
    "LOW_MENTION_SHARE": "낮은 AI 언급률",
    "COMPETITOR_VISIBILITY": "경쟁 병원 노출 우세",
    "SOURCE_SIGNAL_GAP": "AI 참고 근거 자료 부족",
}


def _query_text_of(record: Any) -> str | None:
    """SoV 레코드의 표시용 쿼리 텍스트 — QueryMatrix.query_text 우선, 없으면 타깃명."""
    query = getattr(record, "query", None)
    if query is not None and getattr(query, "query_text", None):
        return query.query_text
    target = getattr(record, "ai_query_target", None)
    if target is not None and getattr(target, "name", None):
        return target.name
    return None


def _source_metrics(records: list[Any], hospital: Hospital | None) -> dict[str, int | float]:
    """Separate answer grounding from citations to the hospital's own channels.

    ``source_backed_count`` answers whether an AI answer exposed any inspectable source.
    The owned-source fields answer the materially different GEO question: whether the
    hospital's official web properties were selected as evidence.
    """
    roots = _owned_source_roots(hospital)
    source_backed_count = 0
    owned_source_count = 0
    source_url_count = 0
    owned_source_url_count = 0
    for record in records:
        urls = [
            value.strip()
            for value in (getattr(record, "source_urls", None) or [])
            if isinstance(value, str) and value.strip()
        ]
        if urls:
            source_backed_count += 1
        owned_urls = [url for url in urls if _matches_owned_source(url, roots)]
        if owned_urls:
            owned_source_count += 1
        source_url_count += len(urls)
        owned_source_url_count += len(owned_urls)
    owned_citation_share_pct = (
        round(owned_source_url_count / source_url_count * 100, 1)
        if source_url_count
        else 0.0
    )
    return {
        "source_backed_count": source_backed_count,
        "owned_source_count": owned_source_count,
        "source_url_count": source_url_count,
        "owned_source_url_count": owned_source_url_count,
        # This is an observational share of captured source URL occurrences, not a rank.
        "owned_citation_share_pct": owned_citation_share_pct,
    }


def _owned_source_roots(hospital: Hospital | None) -> set[tuple[str, str]]:
    if hospital is None:
        return set()
    candidates = [
        getattr(hospital, "website_url", None),
        getattr(hospital, "blog_url", None),
        getattr(hospital, "kakao_channel_url", None),
        getattr(hospital, "google_business_profile_url", None),
        getattr(hospital, "google_maps_url", None),
        getattr(hospital, "naver_place_url", None),
    ]
    if getattr(hospital, "aeo_domain", None):
        candidates.append(f"https://{hospital.aeo_domain}")

    # 자기 도메인이 없는 병원은 플랫폼 기본 주소(경로형·서브도메인형)로 서빙된다.
    # 이 두 형태를 owned 후보에 넣지 않으면 허브가 인용돼도 owned=0으로 집계돼
    # "우리 글이 AI에 읽혔는가"가 구조적으로 항상 0이 된다.
    roots: set[tuple[str, str]] = set(platform_owned_source_roots(hospital))
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        parsed = urlparse(candidate.strip())
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if not host:
            continue
        roots.add((host, parsed.path.rstrip("/")))
    return roots


def _matches_owned_source(url: str, roots: set[tuple[str, str]]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return any(
        host == root_host
        and (
            not root_path
            or path == root_path
            or path.startswith(f"{root_path}/")
        )
        for root_host, root_path in roots
    )


def build_strategy_summary(
    *,
    hospital: Hospital | None = None,
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
        source_metrics = _source_metrics(successful, hospital)
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
            **source_metrics,
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
        and period_start <= action.completed_at < period_end
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
    return sov_engine.record_is_confirmed(record)


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


def report_pdf_filename(
    hospital_slug: str,
    label: str,
    *,
    report_type: str,
    report_version: int | None,
) -> str:
    version_suffix = f"_v{report_version}" if report_type == "MONTHLY" and report_version else ""
    return f"{hospital_slug}_{label}{version_suffix}.pdf"


def generate_pdf_report(
    hospital: Hospital,
    period_start: datetime,
    period_end: datetime,
    report_type: str = "MONTHLY",
    sov_pct: float | None = 0.0,
    published_count: int = 0,
    repeat_count: int = 5,
    attribution: ContentAttributionPayload | None = None,
    strategy: dict[str, Any] | None = None,
    sov_coverage: MonthlySovPayload | None = None,
    content_operations: dict[str, Any] | None = None,
    citations: CitationSummaryPayload | None = None,
    talking_points: list[str] | None = None,
    report_version: int | None = None,
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
    filename = report_pdf_filename(
        hospital.slug,
        label,
        report_type=report_type,
        report_version=report_version,
    )
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
        sov_coverage=sov_coverage,
        content_operations=content_operations,
        # AI 답변이 인용한 자사 URL의 글 단위 귀속(월간 전용). None이면 섹션 미노출 —
        # `citations` 키가 없던 과거 리포트도 그대로 렌더된다.
        citations=citations,
        # AE가 원장 앞에서 그대로 읽을 3문장. 원장 PDF에는 넣지 않는다 —
        # 이건 내부 준비물이지 고객 문서가 아니다.
        talking_points=talking_points or [],
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


# ══════════════════════════════════════════════════════════════════
# 원장용 월간 리포트 뷰 모델
#
# AE용 report.html은 운영 판단에 필요한 것을 다 담아 원장에게는 과적합이다
# (플랫폼별 표, 실행안의 담당·기한 열 등). 같은 데이터를 다른 편집으로 보여준다.
#
# 언어 규칙(VERSIONUP §5-3 결정 ⑤): 원장 화면에서 퍼센트·전문 용어를 쓰지 않는다.
# "100번 물어보면 몇 번"이 비전문가에게 검증된 최선의 설명이고, 합성 점수는 만들지 않는다.
# ══════════════════════════════════════════════════════════════════

# 원장이 자기 폰으로 확인했을 때 방어할 수 있는 최소 길이. 너무 길면 안 읽는다.
DOCTOR_EXCERPT_CHARS = 260

# NORMAL_FLUCTUATION(고정 5) 상수는 제거했다. 근거 없는 상수였고 실측 노이즈의
# 1/4~1/5이라 "정상 범위"라는 말이 사실이 아니었다. 이제 각주의 오차 범위는
# 이번 달 실제 표본으로 계산한 Wilson 구간에서 나오고(services/sov_statistics.py),
# 증감 문장은 그 구간이 겹치는지로 고른다.

_PLATFORM_LABELS = {"chatgpt": "챗GPT", "gemini": "제미나이"}


def _as_hundred(pct: float | None) -> int | None:
    """언급률(%)을 '100번 중 N번'의 N으로. 퍼센트 표기를 화면에서 없애기 위한 변환."""
    return None if pct is None else round(pct)


def _excerpt_around(text: str, needle: str, *, width: int = DOCTOR_EXCERPT_CHARS) -> str:
    """답변 원문에서 병원명 주변을 잘라낸다. 못 찾으면 앞부분을 준다."""
    body = " ".join((text or "").split())
    if not body:
        return ""
    index = body.find(needle) if needle else -1
    if index < 0:
        return body[:width] + ("…" if len(body) > width else "")
    start = max(0, index - width // 3)
    end = min(len(body), start + width)
    return ("…" if start > 0 else "") + body[start:end] + ("…" if end < len(body) else "")


def _platform_label(value: Any) -> str:
    return _PLATFORM_LABELS.get(str(value or "").lower(), str(value or ""))


def _competitors_named_in(record: Any) -> list[str]:
    return [
        str(item.get("name")).strip()
        for item in (getattr(record, "competitor_mentions", None) or [])
        if isinstance(item, dict) and item.get("is_mentioned") and str(item.get("name") or "").strip()
    ]


def _pick_evidence(records: list, hospital_name: str) -> DoctorEvidence:
    """나온 사례 1개와 안 나온 사례 1개.

    이 블록이 리포트의 심장이다 — 원장이 자기 폰으로 물어봤는데 안 나오는 순간이
    반드시 오고, 그때 방어하는 유일한 자산이 저장된 답변 원문이다.
    """
    usable = [r for r in records if _successful_measurement(r)]
    mentioned = next((r for r in usable if getattr(r, "is_mentioned", False)), None)
    missing = next((r for r in usable if not getattr(r, "is_mentioned", False)), None)

    def _shape(record: Any, *, found: bool) -> DoctorEvidenceCase | None:
        if record is None:
            return None
        return {
            "question": _query_text_of(record) or "",
            "excerpt": _excerpt_around(
                getattr(record, "raw_response", "") or "",
                hospital_name if found else "",
            ),
            "platform": _platform_label(getattr(record, "ai_platform", None)),
            "measured_at": getattr(record, "measured_at", None),
            "competitors": [] if found else _competitors_named_in(record)[:3],
        }

    return {"found": _shape(mentioned, found=True), "missing": _shape(missing, found=False)}


def _error_margin_footnote(
    margin_of_hundred: int | None, basis: dict[str, Any]
) -> str:
    """오차 범위 각주 — 고정 상수가 아니라 이번 달 표본에서 계산한 값으로 쓴다.

    표본 정보가 없는 구버전 payload에서는 숫자를 지어내지 않고, AI 답변이
    매번 달라진다는 사실만 남긴다.
    """
    questions = int(basis.get("question_count") or 0)
    repeats = int(basis.get("repeat_count") or 0)
    platform_count = int(basis.get("platform_count") or 0)
    if margin_of_hundred is None or not questions or not repeats:
        return "AI 답변은 같은 질문에도 매번 달라져 횟수가 다소 오르내립니다."
    if platform_count > 1:
        scope = f"질문 {questions}개 × AI 서비스 {platform_count}곳 × 반복 {repeats}회 기준"
    else:
        scope = f"질문 {questions}개 × 반복 {repeats}회 기준"
    return f"이번 달 수치의 오차 범위는 ±{margin_of_hundred}번입니다 ({scope})."


# ── 원장 1페이지의 3막 ─────────────────────────────────────────────────
# 막 1 "이번 달 저희가 한 일" → 막 2 "무엇이 달라졌나" → 막 3 "다음 달 계획".
# 예전에는 막 2만 있었다. 그래서 원장 미팅이 숫자 방어로 시작해 숫자 방어로
# 끝났고, AE에게는 "무엇을 했고 다음엔 무엇을 한다"를 말할 페이지가 없었다.

DOCTOR_ACT1_TITLE_LIMIT = 3
DOCTOR_MENTION_LIST_LIMIT = 3
DOCTOR_TRIMMED_LIST_LIMIT = 2
DOCTOR_APPENDIX_ROW_LIMIT = 15
# 경쟁 병원 이름은 같은 질문에서 2회 이상 관측될 때만 적는다. 1회 관측은 그날
# 답변 하나일 수 있어 원장 앞에서 방어되지 않는다.
DOCTOR_COMPETITOR_MIN_OBSERVATIONS = 2

# 1페이지 트리밍 예산 — CSS overflow에 맡기면 넘친 내용이 **조용히** 잘리고
# 무엇이 잘렸는지 아무도 모른다. 대신 뷰가 결정적인 순서로 덜어내고 무엇을
# 뺐는지 `trimmed`에 남긴다(테스트가 그 순서를 고정한다).
# 값은 실제 WeasyPrint 렌더로 잡았다: 최대 밀도 뷰가 추정 55줄에서 2쪽으로
# 넘쳤고 48줄에서 1쪽에 들어갔다.
DOCTOR_PAGE1_LINE_BUDGET = 48
# 한 줄에 들어가는 대략적인 글자 수. 폰트 크기별로 다르다(본문 9.2pt,
# 인용문 7.8pt, 각주 8pt). 정확한 조판이 아니라 **상한 추정**이면 충분하다.
_PAGE1_BODY_CPL = 46
_PAGE1_EXCERPT_CPL = 62
_PAGE1_FOOTNOTE_CPL = 60
# 머리글·요약 박스·헤드라인 박스·타일·소제목 4개·링크가 쓰는 고정 줄 수.
_PAGE1_FIXED_LINES = 17


def _wrapped_lines(text: str | None, chars_per_line: int) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text) / chars_per_line))


def _content_type_code(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _published_items(
    published_contents: Sequence[Any], cited_titles: set[str]
) -> list[DoctorPublishedItem]:
    """막 1에 이름을 올릴 글. 인용된 글 → 측정 질문을 겨냥한 글 → FAQ 순.

    편수 타일만으로는 "무엇을 했나"에 답이 안 된다. 원장이 제목을 읽어야
    자기 병원 이야기가 된다. 순서는 AI가 실제로 읽은 글을 앞에 세운다.
    """
    ranked: list[tuple[int, str, bool]] = []
    for content in published_contents:
        title = str(getattr(content, "title", "") or "").strip()
        if not title:
            continue
        cited = title in cited_titles
        gap_driven = getattr(content, "query_target_id", None) is not None
        if cited:
            rank = 0
        elif gap_driven:
            rank = 1
        elif _content_type_code(getattr(content, "content_type", None)) == "FAQ":
            rank = 2
        else:
            rank = 3
        ranked.append((rank, title, cited))
    ranked.sort(key=lambda row: (row[0], row[1]))
    items: list[DoctorPublishedItem] = []
    seen: set[str] = set()
    for _rank, title, cited in ranked:
        if title in seen:
            continue
        seen.add(title)
        items.append({"title": title, "cited": cited})
        if len(items) >= DOCTOR_ACT1_TITLE_LIMIT:
            break
    return items


def _citation_line(citations: CitationSummaryPayload | None) -> str | None:
    """"AI가 우리 글을 읽었는가"를 한 줄로. 구버전 리포트에는 없어 생략한다.

    분모는 이번 달 **성공 측정한 답변 수**다. "질문 N개"라고 쓰면 질문×AI 서비스
    조합을 질문 수로 부풀리는 셈이라 쓰지 않는다 — coverage_text와 같은 단위다.
    """
    if not citations:
        return None
    measured = int(citations.get("measured_cell_count") or 0)
    if measured <= 0:
        return None
    cited = int(citations.get("cited_cell_count") or 0)
    return (
        f"AI 답변이 저희 병원 글·페이지를 인용한 횟수: {cited}건"
        f"(확인한 답변 {measured}개 중)"
    )


def _mention_sentences(rows: Sequence[Any], limit: int) -> list[DoctorMentionSentence]:
    return [
        {
            "query_text": str(row.get("query_text") or ""),
            "platform_label": str(row.get("platform_label") or "AI"),
        }
        for row in rows[:limit]
        if str(row.get("query_text") or "").strip()
    ]


def _competitor_by_question(records: Sequence[Any]) -> dict[str, str]:
    """질문별로 가장 많이 언급된 경쟁 병원 이름. 관측 1회짜리는 버린다."""
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        if not _successful_measurement(record):
            continue
        question = _query_text_of(record) or ""
        if not question:
            continue
        bucket = counts.setdefault(question, {})
        for name in dict.fromkeys(_competitors_named_in(record)):
            bucket[name] = bucket.get(name, 0) + 1
    top: dict[str, str] = {}
    for question, bucket in counts.items():
        if not bucket:
            continue
        name, count = min(bucket.items(), key=lambda pair: (-pair[1], pair[0]))
        if count >= DOCTOR_COMPETITOR_MIN_OBSERVATIONS:
            top[question] = name
    return top


def _cited_title_by_question(citations: CitationSummaryPayload | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in (citations or {}).get("cited_items") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        for query in item.get("queries") or []:
            text = str(query.get("query_text") or "").strip()
            if text and text not in mapping:
                mapping[text] = title
    return mapping


def _appendix_count_label(attempts_used: int, mentioned_attempts: int) -> str:
    if attempts_used <= 0:
        return "측정 없음"
    if mentioned_attempts <= 0:
        return "안 나옴"
    return f"{attempts_used}번 중 {mentioned_attempts}번"


def _appendix_rows(
    question_rows: Sequence[QuestionRowPayload],
    *,
    has_prior_month: bool,
    competitors: dict[str, str],
    cited_titles: dict[str, str],
) -> list[DoctorAppendixRow]:
    """2쪽 부록 — 추적 질문 전체를 한 표로. 1페이지가 못 담는 '전부'가 여기 있다."""
    rows: list[DoctorAppendixRow] = []
    for row in question_rows[:DOCTOR_APPENDIX_ROW_LIMIT]:
        text = str(row.get("query_text") or "").strip()
        if not text:
            continue
        rows.append({
            "query_text": text,
            "prev_label": (
                _appendix_count_label(
                    int(row.get("prior_attempts_used") or 0),
                    int(row.get("prior_mentioned_attempts") or 0),
                )
                if has_prior_month
                else "측정 없음"
            ),
            "current_label": _appendix_count_label(
                int(row.get("current_attempts_used") or 0),
                int(row.get("current_mentioned_attempts") or 0),
            ),
            "competitor": competitors.get(text, "—"),
            "cited_title": cited_titles.get(text, "—"),
        })
    return rows


def _medical_safe_lines(lines: Sequence[str]) -> list[str]:
    """공개 문구와 같은 의료광고 금지 표현 검사를 원장 리포트 문장에도 건다.

    지금 문장은 전부 코드 상수라 걸릴 일이 없지만, 게이트가 없으면 다음에
    문구를 고치는 사람이 그 사실을 모른다.
    """
    return [line for line in lines if line and not check_forbidden(line)]


def _v0_footnote() -> str:
    return (
        "서비스 시작 시점(V0)은 지금과 다른 반복 방식으로 측정해 같은 기준의 비교가 "
        "아니라 참고용 값입니다."
    )


def _talking_points(
    *,
    measured: bool,
    published_count: int,
    plan_quota: int | None,
    published_items: Sequence[DoctorPublishedItem],
    citations: CitationSummaryPayload | None,
    this_count: int | None,
    delta_sentence: str,
    lost_count: int,
    ours: Sequence[str],
) -> list[str]:
    """AE가 원장 앞에서 그대로 읽을 수 있는 3문장. 숫자는 전부 뷰에서 바인딩한다."""
    volume = (
        f"이번 달 {published_count}편을 발행했습니다."
        if plan_quota is None
        else f"이번 달 약정 {plan_quota}편 중 {published_count}편을 발행했습니다."
    )
    if published_items:
        volume = f"{volume[:-1]}, 대표 글은 “{published_items[0]['title']}”입니다."
    if citations:
        cited_cells = int(citations.get("cited_cell_count") or 0)
        volume = f"{volume} AI 답변이 저희 글·페이지를 인용한 건수는 {cited_cells}건입니다."
    if measured and this_count is not None:
        change = f"환자 질문 100번 중 병원이 나온 횟수는 {this_count}번이고, {delta_sentence}."
    else:
        change = "이번 달은 측정이 충분히 이뤄지지 않아 횟수를 보고드리지 않습니다."
    if lost_count:
        change = f"{change} 이번 달 빠진 질문은 {lost_count}개입니다."
    plan = f"다음 달 계획: {ours[0]}" if ours else "다음 달 계획은 리포트 본문을 참고해 주세요."
    return _medical_safe_lines([volume, change, plan])


def _page1_line_cost(
    *,
    summary: str,
    coverage_text: str,
    delta_sentence: str,
    v0_baseline: DoctorV0Baseline | None,
    published_items: Sequence[DoctorPublishedItem],
    citation_line: str | None,
    new_mentions: Sequence[DoctorMentionSentence],
    lost_mentions: Sequence[DoctorMentionSentence],
    next_actions: DoctorNextActions,
    evidence: DoctorEvidence,
    footnotes: Sequence[str],
) -> int:
    """1페이지에 들어갈 대략적인 줄 수. 정밀 조판이 아니라 상한 추정이다."""
    total = _PAGE1_FIXED_LINES
    total += _wrapped_lines(summary, _PAGE1_BODY_CPL)
    total += _wrapped_lines(coverage_text, _PAGE1_FOOTNOTE_CPL)
    total += _wrapped_lines(delta_sentence, _PAGE1_BODY_CPL)
    if v0_baseline is not None:
        total += 1
    total += sum(_wrapped_lines(item["title"], _PAGE1_BODY_CPL) for item in published_items)
    total += _wrapped_lines(citation_line, _PAGE1_BODY_CPL)
    for row in (*new_mentions, *lost_mentions):
        total += _wrapped_lines(row["query_text"], _PAGE1_BODY_CPL)
    total += sum(
        _wrapped_lines(line, _PAGE1_BODY_CPL)
        for line in (*next_actions["ours"], *next_actions["yours"])
    )
    for case in (evidence["found"], evidence["missing"]):
        if case is None:
            continue
        total += 1 + _wrapped_lines(case["question"], _PAGE1_BODY_CPL)
        total += _wrapped_lines(case["excerpt"], _PAGE1_EXCERPT_CPL)
    total += sum(_wrapped_lines(note, _PAGE1_FOOTNOTE_CPL) for note in footnotes)
    return total


def build_doctor_report_view(
    *,
    hospital: Any,
    sov_pct: float | None,
    prev_sov_pct: float | None,
    published_count: int,
    plan_quota: int | None,
    attribution: ContentAttributionPayload | None,
    records: list,
    citations: CitationSummaryPayload | None = None,
    published_contents: Sequence[Any] = (),
    v0_baseline: DoctorV0Baseline | None = None,
    platforms: list[str] | None = None,
    sov_coverage: MonthlySovPayload | None = None,
    comparison_reason: str | None = None,
    significance: DeltaSignificance | None = None,
) -> DoctorReportView:
    """원장에게 보낼 1페이지(+선택적 2쪽 부록)의 모든 문구와 숫자를 만든다.

    숫자는 전부 코드 바인딩이다 — 시장 1위 리포팅 툴의 현재 1순위 불만이 AI 요약의
    숫자 환각이라, 이 함수는 LLM을 쓰지 않는다.
    """
    this_count = _as_hundred(sov_pct)
    prev_count = _as_hundred(prev_sov_pct)
    measured = this_count is not None

    coverage = sov_coverage or {}
    # 유의성은 호출부가 명시하지 않으면 월간 payload에서 읽는다. 둘 다 없으면
    # (구버전 payload) 상승·하락을 단정하지 않고 변화만 읽어준다.
    verdict: DeltaSignificance | None = significance or coverage.get("significance")
    margin_of_hundred = coverage.get("margin_of_hundred")
    basis = coverage.get("measurement_basis") or {}

    comparison_is_valid = comparison_reason in (None, "MATCHED_COHORT")
    delta = (
        None
        if this_count is None or prev_count is None or not comparison_is_valid
        else this_count - prev_count
    )
    if not measured:
        delta_sentence = "이번 달은 측정이 충분히 이뤄지지 않았습니다"
    elif comparison_reason == "NO_PRIOR_MANIFEST" or (
        prev_count is None and comparison_reason is None
    ):
        delta_sentence = "이번 달이 기준선입니다"
    elif comparison_reason not in (None, "MATCHED_COHORT"):
        delta_sentence = "측정 기준이 바뀌어 다음 달부터 비교합니다"
    elif delta is None:
        delta_sentence = "이번 달이 기준선입니다"
    else:
        # **증감의 해석은 부호가 아니라 표본이 정한다.** 예전에는 delta > 0이면
        # 무조건 "늘었습니다"였는데, 셀 하나가 뒤집히면 3점이 움직이는 표본에서
        # 그 문장은 노이즈를 성과로 판 것이다. 이제 두 달의 95% 구간이 겹치지
        # 않을 때만 "의미 있는" 변화라고 말한다.
        movement = f"지난달 {prev_count}번 → 이번 달 {this_count}번"
        if verdict == "SIGNIFICANT_UP":
            delta_sentence = f"{movement} (의미 있는 상승입니다)"
        elif verdict == "SIGNIFICANT_DOWN":
            delta_sentence = f"{movement} (의미 있는 하락입니다)"
        elif verdict == "WITHIN_NOISE":
            delta_sentence = f"{movement} (정상 변동 범위 안입니다)"
        elif delta > 0:
            delta_sentence = f"{movement} ({delta}개 늘었습니다)"
        elif delta < 0:
            delta_sentence = f"{movement} ({abs(delta)}개 줄었습니다)"
        else:
            delta_sentence = f"{movement} (변화 없습니다)"

    first_measured_questions = int(
        (attribution or {}).get("first_measured_mention_count") or 0
    )
    non_comparable_questions = int((attribution or {}).get("non_comparable_count") or 0)
    mention_rows = (
        (attribution or {}).get("new_mention_cells")
        or (attribution or {}).get("new_mention_queries")
        or []
    )
    new_mention_sentences = _mention_sentences(mention_rows, DOCTOR_MENTION_LIST_LIMIT)
    # 지난달 manifest가 없으면 "빠진 질문"이라는 말 자체가 성립하지 않는다.
    has_prior_month = bool((attribution or {}).get("has_prior_month"))
    lost_mention_sentences = (
        _mention_sentences(
            (attribution or {}).get("lost_mention_cells") or [],
            DOCTOR_MENTION_LIST_LIMIT,
        )
        if has_prior_month
        else []
    )

    tiles: list[DoctorTile] = [
        {
            "label": "이번 달 발행한 글",
            "value": f"{published_count}편" if plan_quota is None else f"{plan_quota}편 중 {published_count}편",
            "hint": "약정한 편수 대비 진행률입니다.",
        },
    ]

    cited_titles_by_question = _cited_title_by_question(citations)
    published_items = _published_items(
        published_contents, set(cited_titles_by_question.values())
    )
    citation_line = _citation_line(citations)

    if measured:
        summary = (
            f"이번 달 환자 질문 100번 중 AI가 {hospital.name}을(를) 답변에 넣은 횟수는 "
            f"{this_count}번입니다."
        )
    else:
        summary = "이번 달은 측정이 충분히 이뤄지지 않아 횟수를 보고드리지 않습니다."
    if measured and delta_sentence in {
        "이번 달이 기준선입니다",
        "측정 기준이 바뀌어 다음 달부터 비교합니다",
    }:
        summary = f"{delta_sentence}. 이번 달 현재는 환자 질문 100번 중 {this_count}번입니다."

    ours = ["다음 달에도 계획한 글을 예정대로 발행합니다."]
    if lost_mention_sentences:
        ours.append("이번 달 빠진 질문을 먼저 확인해 다음 글의 주제를 정합니다.")
    elif attribution and attribution.get("new_mention_queries"):
        ours.append("아직 병원이 나오지 않는 질문을 겨냥해 다음 글의 주제를 정합니다.")
    else:
        ours.append("아직 병원이 나오지 않는 질문을 추려 다음 글의 주제를 정합니다.")
    next_actions: DoctorNextActions = {
        "ours": _medical_safe_lines(ours)[:2],
        "yours": _medical_safe_lines(
            ["월 1회 30분 통화로 요즘 환자분들이 많이 묻는 것을 알려주세요."]
        )[:1],
    }

    platform_names = ", ".join(_platform_label(p) for p in (platforms or [])) or "챗GPT, 제미나이"
    if sov_coverage is None:
        coverage_text = f"측정 범위: {platform_names}에서 확인한 AI 답변을 사용했습니다."
    else:
        coverage_text = (
            f"측정 범위: {platform_names}에서 계획한 답변 {sov_coverage['planned_count']}개 중 "
            f"{sov_coverage['success_count']}개를 확인했습니다."
        )

    footnotes = [
        f"{platform_names}에 환자들이 실제로 쓰는 표현으로 질문해 답변을 모았습니다.",
        _error_margin_footnote(margin_of_hundred, basis),
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
    ]
    if v0_baseline is not None:
        footnotes.append(_v0_footnote())
    if first_measured_questions:
        footnotes.append(
            f"이번 달 처음 확인된 질문 {first_measured_questions}개는 지난달 결과가 없어 "
            "새로 좋아진 결과로 계산하지 않았습니다."
        )
    if non_comparable_questions:
        footnotes.append(
            f"지난달 측정이 끝나지 않은 질문 {non_comparable_questions}개는 비교에서 제외했습니다. "
            "다음 달 정상 측정 후 비교합니다."
        )

    evidence = _pick_evidence(records, getattr(hospital, "name", "") or "")

    # ── 1페이지 트리밍 ────────────────────────────────────────────────
    # 넘칠 때 무엇을 먼저 버릴지는 편집 결정이지 CSS 결정이 아니다.
    # 순서: ① 안 나온 사례 → ② 새 질문 2개로 → ③ 빠진 질문 2개로
    #      → ④ 글 제목 2개로 → ⑤ 나온 사례.
    trimmed: list[str] = []

    def _cost() -> int:
        return _page1_line_cost(
            summary=summary,
            coverage_text=coverage_text,
            delta_sentence=delta_sentence,
            v0_baseline=v0_baseline,
            published_items=published_items,
            citation_line=citation_line,
            new_mentions=new_mention_sentences,
            lost_mentions=lost_mention_sentences,
            next_actions=next_actions,
            evidence=evidence,
            footnotes=footnotes,
        )

    def _drop_missing_evidence() -> bool:
        if evidence["missing"] is None:
            return False
        evidence["missing"] = None
        return True

    def _shrink_new_mentions() -> bool:
        if len(new_mention_sentences) <= DOCTOR_TRIMMED_LIST_LIMIT:
            return False
        del new_mention_sentences[DOCTOR_TRIMMED_LIST_LIMIT:]
        return True

    def _shrink_lost_mentions() -> bool:
        if len(lost_mention_sentences) <= DOCTOR_TRIMMED_LIST_LIMIT:
            return False
        del lost_mention_sentences[DOCTOR_TRIMMED_LIST_LIMIT:]
        return True

    def _shrink_published_items() -> bool:
        if len(published_items) <= DOCTOR_TRIMMED_LIST_LIMIT:
            return False
        del published_items[DOCTOR_TRIMMED_LIST_LIMIT:]
        return True

    def _drop_found_evidence() -> bool:
        if evidence["found"] is None:
            return False
        evidence["found"] = None
        return True

    for label, step in (
        ("EVIDENCE_MISSING", _drop_missing_evidence),
        ("NEW_MENTIONS", _shrink_new_mentions),
        ("LOST_MENTIONS", _shrink_lost_mentions),
        ("PUBLISHED_TITLES", _shrink_published_items),
        ("EVIDENCE_FOUND", _drop_found_evidence),
    ):
        if _cost() <= DOCTOR_PAGE1_LINE_BUDGET:
            break
        if step():
            trimmed.append(label)

    appendix_rows = _appendix_rows(
        (attribution or {}).get("question_rows") or [],
        has_prior_month=has_prior_month,
        competitors=_competitor_by_question(records),
        cited_titles=cited_titles_by_question,
    )

    return {
        "measured": measured,
        "hospital_name": hospital.name,
        "headline": {
            "of_hundred": this_count,
            "prev_of_hundred": prev_count,
            "delta": delta,
            "delta_sentence": delta_sentence,
            # 숫자의 실체 — 반복 표본 크기, 빈도, 95% 구간, 유의성 판정.
            # 템플릿은 쓰지 않지만 Admin·미팅 자료가 같은 근거를 보게 한다.
            "attempts_used": coverage.get("attempts_used"),
            "mention_frequency": coverage.get("mention_frequency"),
            "ci95_low_of_hundred": _as_hundred(coverage.get("ci95_low")),
            "ci95_high_of_hundred": _as_hundred(coverage.get("ci95_high")),
            "margin_of_hundred": margin_of_hundred,
            "significance": verdict,
        },
        "summary": summary,
        "coverage_text": coverage_text,
        "tiles": tiles,
        "published_items": published_items,
        "citation_line": citation_line,
        "new_mention_sentences": new_mention_sentences,
        "new_mention_empty_text": (
            "이번 달에는 지난달과 같은 질문에서 새로 확인된 병원 언급이 없습니다."
        ),
        "lost_mention_sentences": lost_mention_sentences,
        "v0_baseline": v0_baseline,
        "evidence": evidence,
        "next_actions": next_actions,
        "footnotes": footnotes,
        "appendix_rows": appendix_rows,
        "trimmed": trimmed,
        "cited_content_count": int((citations or {}).get("cited_content_count") or 0),
        "cited_cells": int((citations or {}).get("cited_cell_count") or 0),
        "top_cited_items": [
            {
                "title": (str(row.get("title")).strip() if row.get("title") else None),
                "cited_cell_count": int(row.get("cited_cell_count") or 0),
            }
            for row in ((citations or {}).get("cited_items") or [])[:3]
        ],
        "talking_points": _talking_points(
            measured=measured,
            published_count=published_count,
            plan_quota=plan_quota,
            published_items=published_items,
            citations=citations,
            this_count=this_count,
            delta_sentence=delta_sentence,
            lost_count=int((attribution or {}).get("lost_mention_count") or 0),
            ours=next_actions["ours"],
        ),
    }
