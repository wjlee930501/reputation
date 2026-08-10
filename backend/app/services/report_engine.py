"""PDF 리포트 생성 엔진 — V0 및 월간 리포트"""
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import arrow
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.models.hospital import Hospital
from app.services.doctor_pdf_contracts import (
    DoctorEvidence,
    DoctorEvidenceCase,
    DoctorReportView,
    DoctorTile,
)
from app.services.monthly_sov_types import MonthlySovPayload
from app.services.report_attribution import ContentAttributionPayload

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
    attribution: ContentAttributionPayload | None = None,
    strategy: dict[str, Any] | None = None,
    sov_coverage: MonthlySovPayload | None = None,
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
        sov_coverage=sov_coverage,
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

# 월간 변동이 이 폭 안이면 "정상 범위"로 안내한다. 안 쓰면 다음 달 하락이 해지 대화가 된다.
NORMAL_FLUCTUATION = 5

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


def build_doctor_report_view(
    *,
    hospital: Any,
    sov_pct: float | None,
    prev_sov_pct: float | None,
    published_count: int,
    plan_quota: int | None,
    attribution: ContentAttributionPayload | None,
    records: list,
    platforms: list[str] | None = None,
    sov_coverage: MonthlySovPayload | None = None,
) -> DoctorReportView:
    """원장에게 보낼 1페이지의 모든 문구와 숫자를 만든다.

    숫자는 전부 코드 바인딩이다 — 시장 1위 리포팅 툴의 현재 1순위 불만이 AI 요약의
    숫자 환각이라, 이 함수는 LLM을 쓰지 않는다.
    """
    this_count = _as_hundred(sov_pct)
    prev_count = _as_hundred(prev_sov_pct)
    measured = this_count is not None

    delta = None if (this_count is None or prev_count is None) else this_count - prev_count
    if delta is None:
        delta_sentence = None
    elif delta > 0:
        delta_sentence = f"전월 {prev_count}번 → 이번 달 {this_count}번 ({delta}개 늘었습니다)"
    elif delta < 0:
        delta_sentence = f"전월 {prev_count}번 → 이번 달 {this_count}번 ({abs(delta)}개 줄었습니다)"
    else:
        delta_sentence = f"전월 {prev_count}번 → 이번 달 {this_count}번 (변화 없습니다)"

    new_questions = int((attribution or {}).get("new_mention_count") or 0)
    first_measured_questions = int(
        (attribution or {}).get("first_measured_mention_count") or 0
    )
    non_comparable_questions = int((attribution or {}).get("non_comparable_count") or 0)
    ahead_of = _competitor_outcomes(records)

    tiles: list[DoctorTile] = [
        {
            "label": "이번 달 발행한 글",
            "value": f"{published_count}편" if plan_quota is None else f"{plan_quota}편 중 {published_count}편",
            "hint": "약정한 편수 대비 진행률입니다.",
        },
        {
            "label": "지난달 기준에서 새로 확인된 병원 언급",
            "value": f"{new_questions}개",
            "hint": "지난달에도 정상 확인한 같은 질문에는 없었지만 이번 달에는 나온 경우입니다.",
        },
    ]
    if ahead_of:
        top = ahead_of[0]
        tiles.append({
            "label": "가장 많이 언급된 다른 병원",
            "value": top["name"],
            "hint": f"같은 질문들에서 {top['mention_count']}번 언급됐습니다.",
        })

    if measured:
        summary = (
            f"이번 달 환자 질문 100번 중 AI가 {hospital.name}을(를) 답변에 넣은 횟수는 "
            f"{this_count}번입니다."
        )
    else:
        summary = "이번 달은 측정이 충분히 이뤄지지 않아 횟수를 보고드리지 않습니다."

    ours = ["다음 달에도 계획한 글을 예정대로 발행합니다."]
    if attribution and attribution.get("new_mention_queries"):
        ours.append("아직 병원이 나오지 않는 질문을 겨냥해 다음 글의 주제를 정합니다.")
    else:
        ours.append("아직 병원이 나오지 않는 질문을 추려 다음 글의 주제를 정합니다.")

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
        f"횟수가 {NORMAL_FLUCTUATION}개 안팎으로 오르내리는 것은 정상 범위입니다.",
        "이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
    ]
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

    return {
        "measured": measured,
        "hospital_name": hospital.name,
        "headline": {
            "of_hundred": this_count,
            "prev_of_hundred": prev_count,
            "delta": delta,
            "delta_sentence": delta_sentence,
        },
        "summary": summary,
        "coverage_text": coverage_text,
        "tiles": tiles,
        "evidence": _pick_evidence(records, getattr(hospital, "name", "") or ""),
        "next_actions": {
            "ours": ours,
            "yours": ["월 1회 30분 통화로 요즘 환자분들이 많이 묻는 것을 알려주세요."],
        },
        "footnotes": footnotes,
    }
