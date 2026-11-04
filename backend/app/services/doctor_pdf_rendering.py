"""Render complete customer reports and validate the exact uploaded bytes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from app.services.doctor_pdf_contracts import (
    DoctorPdfExpectation,
    DoctorPdfValidationError,
    DoctorReportView,
    ValidatedDoctorPdf,
)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
MAX_DOCTOR_PAGES = 32


def _layout_is_valid(document, *, appendix: bool, row_count: int = 0) -> bool:
    """Use public page anchors rather than a guessed character-count budget."""
    if not document.pages or len(document.pages) > MAX_DOCTOR_PAGES:
        return False
    if "overview-end" not in document.pages[0].anchors:
        return False
    if appendix:
        if len(document.pages) < 2 or "appendix-start" not in document.pages[1].anchors:
            return False
    elif len(document.pages) != 1:
        return False
    anchors = {name for page in document.pages for name in page.anchors}
    if any(
        f"appendix-row-{index}" not in anchors or f"appendix-row-{index}-end" not in anchors
        for index in range(row_count)
    ):
        return False
    for page in document.pages:
        for name, point in page.anchors.items():
            if name == "overview-end" or name.startswith("appendix-row-"):
                x, y = point[:2]
                if not 0 <= x <= page.width or not 0 <= y <= page.height - 56:
                    return False
    return True


def safe_public_url(value: str) -> bool:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        )
    except ValueError:
        return False


def render_validated_doctor_pdf(
    *,
    view: DoctorReportView,
    period_label: str,
    public_url: str,
    expectation: DoctorPdfExpectation,
) -> ValidatedDoctorPdf:
    """Two bounded layout attempts; no measurement or provider calls during layout."""
    if public_url != expectation.public_url or not safe_public_url(public_url):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_PUBLIC_URL_INVALID",
            "안전한 병원 공개 주소를 확인하지 못해 PDF 링크를 만들지 않았습니다.",
        )
    from app.services.doctor_report_v4 import DoctorReportV4

    if isinstance(view, DoctorReportV4):
        return _render_v4(
            view=view, period_label=period_label, public_url=public_url, expectation=expectation
        )
    # Summary quotes are excerpts, never a full-answer transcript. Bound both
    # polarities identically; the complete question table is never shortened.
    view = deepcopy(view)
    v3 = view.get("report_kind", "LEGACY") != "LEGACY"
    if v3 and "narrative" not in view:
        raise DoctorPdfValidationError("DOCTOR_PDF_NARRATIVE_MISSING", "보고서 서술 계약이 없습니다.")
    if v3:
        from app.services.doctor_pdf_v3 import validate_v3_fields
        validate_v3_fields(view)
    if len(view["hospital_name"]) > 200:
        raise DoctorPdfValidationError("DOCTOR_PDF_LAYOUT_LIMIT", "병원명이 지원 길이를 넘었습니다.")
    for case in (() if v3 else (view.get("evidence") or {}).values()):
        if isinstance(case, dict):
            for field, limit in (("question", 100), ("excerpt", 190)):
                value = str(case.get(field) or "")
                if len(value) > limit:
                    case[field] = value[:limit].rstrip() + "…"
    rows = list(view.get("appendix_rows") or [])
    if bool(rows) != expectation.appendix_expected:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_PAGE_COUNT_INVALID", "부록 유무와 검증 기준이 일치하지 않습니다."
        )
    if len(rows) > 512 or any(len(str(value)) > 2000 for row in rows for value in row.values()):
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_LAYOUT_LIMIT",
            "질문표가 지원 문서 크기를 넘었습니다. 결과를 누락하지 않고 중단했습니다.",
        )
    try:
        from weasyprint import HTML

        environment = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(enabled_extensions=("html",)),
        )
        template = environment.get_template("doctor_report_v3.html" if v3 else "doctor_report.html")
        from app.services.doctor_pdf_v3 import v3_layout_valid
        document = None
        for density in ("comfortable", "compact"):
            html = template.render(
                view=view, period_label=period_label, public_url=public_url, density=density
            )
            if v3:
                from app.services.report_typography import keep_korean_words
                html = keep_korean_words(html)
            candidate = HTML(string=html, base_url=str(_TEMPLATE_DIR)).render()
            valid = v3_layout_valid(candidate, len(rows)) if v3 else _layout_is_valid(candidate, appendix=bool(rows), row_count=len(rows))
            if valid:
                document = candidate
                break
        if document is None:
            raise DoctorPdfValidationError(
                "DOCTOR_PDF_LAYOUT_OVERFLOW",
                "요약면 또는 질문표가 페이지 범위를 넘었습니다. 내용을 자르지 않고 중단했습니다.",
            )
        pdf_bytes = bytes(document.write_pdf())
    except DoctorPdfValidationError:
        raise
    except Exception as exc:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_RENDER_FAILED", "원장 전달용 PDF를 만드는 중 오류가 발생했습니다."
        ) from exc
    from app.services.report_artifact_validation import validate_doctor_pdf

    tiles = tuple(
        str(tile[field])
        for tile in view.get("tiles", ())
        for field in ("label", "value", "hint")
        if tile.get(field)
    )
    bound = replace(
        expectation,
        period_label=period_label,
        expected_page_count=len(document.pages),
        required_overview_texts=(
            *expectation.required_overview_texts,
            *tiles,
            str(view.get("summary") or ""),
            str((view.get("headline") or {}).get("delta_sentence") or ""),
            *view.get("footnotes", ()),
        ),
        required_appendix_rows=tuple(
            tuple(
                str(row[field])
                for field in (
                    "query_text",
                    "prev_label",
                    "current_label",
                    "competitor",
                    "cited_title",
                )
                if row.get(field)
            )
            for row in rows
        ),
        required_appendix_texts=tuple(
            str(row[field])
            for row in rows
            for field in ("query_text", "prev_label", "current_label", "competitor", "cited_title")
            if row.get(field)
        ),
    )
    if v3:
        from app.services.doctor_pdf_v3 import v3_expectation
        bound = v3_expectation(view, bound, len(document.pages))
    metadata = validate_doctor_pdf(pdf_bytes, bound)
    return ValidatedDoctorPdf(
        pdf_bytes=pdf_bytes, sha256=metadata.sha256, byte_size=metadata.byte_size, metadata=metadata
    )


def mark_highlight(excerpt: str, highlight: str) -> Markup:
    """발췌 안의 강조 구간만 <mark>로 감싼다. 강조가 없거나 발췌에 없으면 그대로 escape한다."""
    text = str(excerpt or "")
    needle = str(highlight or "")
    if not needle or needle not in text:
        return Markup(escape(text))
    before, _, after = text.partition(needle)
    return Markup(f"{escape(before)}<mark>{escape(needle)}</mark>{escape(after)}")


def pct(value: float) -> str:
    """막대·추이 값 표기. 뷰 모델 `_pct`와 같은 규칙(100은 '100.0%')이라 `facts()`와 글자가 같다."""
    from app.services.doctor_report_v4 import _pct

    return _pct(float(value))


def render_v4_html(
    view, *, period_label: str, public_url: str, density: str = "comfortable", breaks=None
) -> str:
    """v4 템플릿 HTML(한국어 낱말 보존 포함). 렌더러와 샘플 스크립트·테스트가 같은 경로를 쓴다.

    `breaks`는 쪽 넘김 정리(`doctor_pdf_pagination.PageBreaks`)가 정한 강제 넘김 — 없으면 빈 값.
    """
    from app.services.doctor_pdf_pagination import PageBreaks
    from app.services.report_typography import keep_korean_words

    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html",)),
    )
    environment.filters["mark_highlight"] = mark_highlight
    environment.filters["pct"] = pct
    template = environment.get_template("doctor_report_v4.html")
    return keep_korean_words(
        template.render(
            view=view, period_label=period_label, public_url=public_url, density=density,
            breaks=breaks or PageBreaks(),
        )
    )


def layout_v4(
    view, *, period_label: str, public_url: str, density: str = "comfortable", fix_orphans: bool = True
):
    """밀도 하나로 v4를 그린다 — 쪽 넘김 정리를 수렴할 때까지(최대 MAX_PASSES) 되풀이한 레이아웃과 강제 넘김.

    `fix_orphans=False`면 정리 없이 한 번만 그린다(정리가 쪽 상한을 넘기는 극단 입력의 물러서기).
    """
    from weasyprint import HTML

    from app.services.doctor_pdf_pagination import MAX_PASSES, PageBreaks, plan_fixups

    breaks = PageBreaks()
    document = None
    for _ in range(MAX_PASSES if fix_orphans else 1):
        html = render_v4_html(view, period_label=period_label, public_url=public_url, density=density, breaks=breaks)
        document = HTML(string=html, base_url=str(_TEMPLATE_DIR)).render()
        if not fix_orphans:
            break
        fixed = plan_fixups(document, breaks)
        if fixed is None:
            break
        breaks = fixed
    return document, breaks


def _render_v4(*, view, period_label: str, public_url: str, expectation: DoctorPdfExpectation) -> ValidatedDoctorPdf:
    """v4(설계 2026-11-04): 뷰 모델 하나, 쪽별 앵커, 필수 문구는 `required_texts()`에서."""
    from app.services.doctor_pdf_v4 import (
        required_table_anchors,
        section_pages,
        v4_expectation,
        v4_layout_valid,
        validate_v4_fields,
    )

    validate_v4_fields(view)
    row_count = len(view.appendix.keyword_rows)
    table_anchors = required_table_anchors(view)
    try:
        document = None
        # 고아 정리는 최선의 노력이다 — 정리한 레이아웃이 쪽 상한을 넘기면(극단 입력) 정리 없는 레이아웃으로 물러선다.
        # 순서: comfortable(정리) → compact(정리) → comfortable(없음) → compact(없음).
        for fix_orphans in (True, False):
            for density in ("comfortable", "compact"):
                candidate, _ = layout_v4(
                    view, period_label=period_label, public_url=public_url, density=density, fix_orphans=fix_orphans
                )
                if v4_layout_valid(candidate, row_count, required_anchors=table_anchors):
                    document = candidate
                    break
            if document is not None:
                break
        if document is None:
            raise DoctorPdfValidationError(
                "DOCTOR_PDF_LAYOUT_OVERFLOW",
                "본문 쪽이나 키워드표가 페이지 범위를 넘었습니다. 내용을 자르지 않고 중단했습니다.",
            )
        pdf_bytes = bytes(document.write_pdf())
    except DoctorPdfValidationError:
        raise
    except Exception as exc:
        raise DoctorPdfValidationError(
            "DOCTOR_PDF_RENDER_FAILED", "원장 전달용 PDF를 만드는 중 오류가 발생했습니다."
        ) from exc
    from app.services.report_artifact_validation import validate_doctor_pdf

    bound = v4_expectation(
        view, replace(expectation, period_label=period_label), len(document.pages), section_pages(document)
    )
    metadata = validate_doctor_pdf(pdf_bytes, bound)
    return ValidatedDoctorPdf(
        pdf_bytes=pdf_bytes, sha256=metadata.sha256, byte_size=metadata.byte_size, metadata=metadata
    )
