"""PDF 리포트 생성 엔진 — V0 및 월간 리포트"""
import logging
from datetime import datetime
from pathlib import Path

import arrow
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.models.hospital import Hospital

logger = logging.getLogger(__name__)
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def generate_pdf_report(
    db,
    hospital: Hospital,
    period_start: datetime,
    period_end: datetime,
    report_type: str = "MONTHLY",
    sov_pct: float = 0.0,
    published_count: int = 0,
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
        published_count=published_count,
        generated_at=now.datetime,
    )

    HTML(string=html).write_pdf(str(local_pdf_path))
    logger.info(f"PDF generated: {local_pdf_path}")

    # GCS 업로드
    gcs_path = _upload_to_gcs(local_pdf_path, hospital.slug, filename)

    # 업로드 후 로컬 파일 삭제
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
        logger.error(f"GCS upload failed, falling back to local path: {e}")
        # GCS 실패 시 로컬 경로 반환 (개발 환경 호환)
        return str(local_path)
