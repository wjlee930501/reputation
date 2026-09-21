# /// script
# requires-python = ">=3.11"
# ///
# How to run (repository root; uses the frozen backend environment):
# REPUTATION_DISABLE_DOTENV=1 DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
# uv run --project backend --frozen --no-sync python scripts/report_design_samples.py
"""Generate fictional HTML/PDF through the actual production renderers, offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

os.environ["REPUTATION_DISABLE_DOTENV"] = "1"
os.environ["APP_ENV"] = "test"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from report_design_fixtures import PUBLIC_URL, diagnosis_sample, monthly_sample  # noqa: E402

from app.services.doctor_pdf_contracts import DoctorPdfExpectation  # noqa: E402
from app.services.doctor_pdf_rendering import render_validated_doctor_pdf  # noqa: E402
from app.services.lead_report import render_lead_report_html, render_lead_report_pdf  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/report-redesign-fictional"
    )
    parser.add_argument("--png", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.INFO)
    templates = ROOT / "backend/app/templates"
    environment = Environment(
        loader=FileSystemLoader(templates), autoescape=select_autoescape(("html",))
    )
    manifest = []
    for name, state, dense in (
        ("monthly-growth", "growth", False),
        ("monthly-decline", "decline", False),
        ("monthly-flat", "flat", False),
        ("monthly-unavailable", "unavailable", False),
        ("monthly-dense", "growth", True),
    ):
        view = monthly_sample(state, dense=dense)
        expectation = DoctorPdfExpectation(
            hospital_name=view["hospital_name"],
            coverage_text=view["coverage_text"],
            caveat_text="이 결과는 진료의 질을 평가하거나 환자 수 증가를 보장하지 않습니다.",
            public_url=PUBLIC_URL,
            appendix_expected=bool(view["appendix_rows"]),
        )
        rendered = render_validated_doctor_pdf(
            view=view,
            period_label="2026-08",
            public_url=PUBLIC_URL,
            expectation=expectation,
        )
        html = environment.get_template("doctor_report_v3.html").render(
            view=view, period_label="2026-08", public_url=PUBLIC_URL
        )
        manifest.append(
            write_sample(
                args.output, name, html, rendered.pdf_bytes, args.png, templates
            )
        )
    for name, state, internal in (
        ("diagnosis-low", "low", False),
        ("diagnosis-strong", "strong", False),
        ("diagnosis-unavailable", "unavailable", False),
        ("diagnosis-internal", "low", True),
    ):
        payload = diagnosis_sample(state, internal=internal)
        manifest.append(
            write_sample(
                args.output,
                name,
                render_lead_report_html(payload),
                render_lead_report_pdf(payload),
                args.png,
                templates,
            )
        )
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def write_sample(
    directory: Path, name: str, html: str, data: bytes, png: bool, templates: Path
) -> dict[str, str | int]:
    html = html.replace(
        "../assets/fonts/PretendardVariable.woff2",
        (templates.parent / "assets/fonts/PretendardVariable.woff2").as_uri(),
    )
    pdf = directory / f"{name}.pdf"
    pdf.write_bytes(data)
    reader = PdfReader(BytesIO(data))
    texts = [page.extract_text() or "" for page in reader.pages]
    assert all("검증용가상사례" in "".join(text.split()) for text in texts)
    if png:
        (directory / f"{name}.html").write_text(html)
        (directory / f"{name}.txt").write_text("\n\n".join(texts))
        for stale in directory.glob(f"{name}-*.png"):
            stale.unlink()
        subprocess.run(
            ["pdftoppm", "-scale-to", "1200", "-png", str(pdf), str(directory / name)],
            check=True,
            capture_output=True,
        )
    return {
        "name": name,
        "pages": len(reader.pages),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


if __name__ == "__main__":
    main()
