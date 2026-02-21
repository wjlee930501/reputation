"""PDF 리포트 생성 엔진 — V0 및 월간 리포트"""
import logging
from datetime import datetime
from pathlib import Path

import arrow
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.hospital import Hospital

logger = logging.getLogger(__name__)
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


async def generate_pdf_report(
    db: AsyncSession,
    hospital: Hospital,
    period_start: datetime,
    period_end: datetime,
    report_type: str = "MONTHLY",
    sov_pct: float = 0.0,
    published_count: int = 0,
) -> str:
    from weasyprint import HTML

    output_dir = Path(settings.REPORT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    now = arrow.now("Asia/Seoul")
    label = "V0-진단" if report_type == "V0" else arrow.get(period_start).format("YYYY-MM")
    filename = f"{hospital.slug}_{label}.pdf"
    pdf_path = str(output_dir / filename)

    # TODO(security): autoescape=True 추가 권장 — PDF 전용이라 XSS 위험은 낮으나 방어적으로 적용 가능
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
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

    HTML(string=html).write_pdf(pdf_path)
    logger.info(f"PDF generated: {pdf_path}")
    return pdf_path
