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


def generate_pdf_report(
    hospital: Hospital,
    period_start: datetime,
    period_end: datetime,
    report_type: str = "MONTHLY",
    sov_pct: float | None = 0.0,
    published_count: int = 0,
    repeat_count: int = 5,
    attribution: dict[str, Any] | None = None,
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
