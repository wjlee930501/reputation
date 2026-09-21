"""Content-ID attribution and actual hospital routes, never title matching."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.services.content_citations import hospital_surface_roots
from app.services.doctor_pdf_contracts import DoctorPdfValidationError
from app.services.doctor_pdf_rendering import safe_public_url
from app.services.report_attribution import CitationSummaryPayload
from app.services.report_narrative import PublishedWork


class ReportHospital(Protocol):
    id: UUID
    slug: str
    aeo_domain: str | None


class ReportContent(Protocol):
    id: UUID
    hospital_id: UUID
    title: str | None
    status: str


def published_work_evidence(
    hospital: ReportHospital,
    contents: Sequence[ReportContent],
    citations: CitationSummaryPayload | None,
) -> tuple[PublishedWork, ...]:
    cited = {row["content_id"].lower(): row for row in (citations or {}).get("cited_items", [])}
    roots = hospital_surface_roots(hospital)
    works: list[PublishedWork] = []
    for item in contents:
        identifier = str(getattr(item, "id", ""))
        same_hospital = (
            getattr(hospital, "id", None) is not None
            and getattr(item, "hospital_id", None) == hospital.id
        )
        matched = cited.get(identifier.lower()) if same_hospital else None
        url = None
        if (
            roots
            and identifier
            and getattr(item, "hospital_id", None) == getattr(hospital, "id", None)
            and getattr(hospital, "id", None) is not None
            and getattr(item, "status", None) == "PUBLISHED"
        ):
            try:
                UUID(identifier)
            except ValueError as exc:
                raise DoctorPdfValidationError(
                    "DOCTOR_PDF_CONTENT_ID_INVALID", "공개 글 식별자를 확인할 수 없습니다."
                ) from exc
            root = roots[0]
            candidate = f"https://{root.host}{root.base_path}/contents/{identifier}"
            if safe_public_url(candidate):
                url = candidate
        works.append(
            PublishedWork(
                title=str(item.title or "제목 없음"),
                content_id=identifier,
                url=url,
                cited_cells=(
                    matched["cited_cell_count"] if matched else 0
                ) if citations and citations.get("measured_cell_count", 0) > 0 else None,
                queries=tuple(
                    f"{row['query_text']} · {row['platform_label']}" for row in matched["queries"]
                )
                if matched
                else (),
            )
        )
    return tuple(works)
