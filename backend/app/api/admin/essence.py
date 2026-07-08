"""Admin API — hospital source-backed content operating standard."""
import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.essence import (
    PHOTO_SOURCE_TYPES,
    HospitalContentPhilosophy,
    HospitalSourceAsset,
    HospitalSourceEvidenceNote,
    PhilosophyStatus,
    SourceStatus,
    SourceType,
)
from app.models.hospital import Hospital, HospitalStatus
from app.schemas.essence import (
    ApprovedPhilosophyResponse,
    PhilosophyApprove,
    PhilosophyDraftCreate,
    PhilosophyPatch,
    PhilosophyResponse,
    SourceAssetCreate,
    SourceAssetPatch,
    SourceAssetResponse,
    SourcePublicToggle,
)
from app.services.asset_extractor import (
    detect_extractor_for,
    extract_docx_text,
    extract_pdf_text,
    fetch_naver_blog_post_urls,
    fetch_url_text,
    naver_blog_id_from,
)
from app.services.asset_storage import resolve_legacy_asset_path, resolve_local_asset_path, store_asset_bytes
from app.services.audit_log import default_actor, write_audit_log
from app.services.essence_engine import (
    compute_source_content_hash,
    find_error_marker_fields,
    process_source_asset,
    synthesize_philosophy,
    validate_philosophy_grounding,
    validate_source_excerpt,
)
from app.services.gcs_utils import get_signed_url
from app.services.site_revalidate import (
    ensure_site_revalidate_configured,
    trigger_hospital_site_revalidate_safe,
)

MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # 12MB


class SourceCrawlRequest(BaseModel):
    source_type: SourceType
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=10, max_length=1000)
    operator_note: str | None = None
    created_by: str | None = Field(default=None, max_length=100)


class BlogCrawlRequest(BaseModel):
    url: str = Field(min_length=2, max_length=1000)  # 블로그 URL 또는 blogId
    max_posts: int = Field(default=10, ge=1, le=15)
    operator_note: str | None = None
    created_by: str | None = Field(default=None, max_length=100)


class BlogCrawlResult(BaseModel):
    blog_id: str | None
    requested: int
    created: int
    skipped_duplicate: int
    skipped_empty: int
    failed: list[dict[str, str]]
    source_ids: list[str]


router = APIRouter(prefix="/admin/hospitals/{hospital_id}/essence", tags=["Admin — Essence"])

SOURCE_TYPE_DISPLAY_LABELS = {
    SourceType.NAVER_BLOG: "네이버 블로그",
    SourceType.YOUTUBE: "유튜브",
    SourceType.HOMEPAGE: "병원 홈페이지",
    SourceType.INTERVIEW: "원장 인터뷰",
    SourceType.LANDING_PAGE: "랜딩 페이지",
    SourceType.BROCHURE: "브로슈어",
    SourceType.INTERNAL_NOTE: "내부 메모",
    SourceType.PHOTO_DOCTOR: "사진 — 원장",
    SourceType.PHOTO_CLINIC_EXTERIOR: "사진 — 병원 외관",
    SourceType.PHOTO_CLINIC_INTERIOR: "사진 — 병원 내부",
    SourceType.PHOTO_TREATMENT_ROOM: "사진 — 진료/시술실",
    SourceType.OTHER: "기타 자료",
}
SOURCE_STATUS_DISPLAY_LABELS = {
    SourceStatus.PENDING: "대기",
    SourceStatus.PROCESSED: "처리완료",
    SourceStatus.EXCLUDED: "제외",
    SourceStatus.ERROR: "오류",
}
PHILOSOPHY_STATUS_DISPLAY_LABELS = {
    PhilosophyStatus.APPROVED: "승인됨",
    PhilosophyStatus.DRAFT: "초안",
    PhilosophyStatus.ARCHIVED: "보관됨",
}


def _display_label(labels: dict, value) -> str | None:
    if value is None:
        return None
    return labels.get(value) or labels.get(str(value)) or labels.get(str(value).upper()) or str(value)


@router.get("/sources", response_model=list[SourceAssetResponse])
async def list_sources(
    hospital_id: uuid.UUID,
    status_filter: SourceStatus | None = Query(default=None, alias="status"),
    source_type: SourceType | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    await _get_hospital_or_404(db, hospital_id)
    stmt = select(HospitalSourceAsset).where(HospitalSourceAsset.hospital_id == hospital_id)
    if status_filter:
        stmt = stmt.where(HospitalSourceAsset.status == status_filter)
    if source_type:
        stmt = stmt.where(HospitalSourceAsset.source_type == source_type)
    stmt = stmt.order_by(HospitalSourceAsset.created_at.desc())

    result = await db.execute(stmt)
    sources = result.scalars().all()
    counts = await _note_counts(db, [source.id for source in sources])
    return [_serialize_source(source, evidence_note_count=counts.get(source.id, 0)) for source in sources]


@router.post("/sources", status_code=status.HTTP_201_CREATED, response_model=SourceAssetResponse)
async def create_source(
    hospital_id: uuid.UUID,
    body: SourceAssetCreate,
    db: AsyncSession = Depends(get_db),
):
    await _get_hospital_or_404(db, hospital_id)
    source = HospitalSourceAsset(
        hospital_id=hospital_id,
        source_type=body.source_type,
        title=body.title,
        url=_clean_optional(body.url),
        raw_text=_clean_optional(body.raw_text),
        operator_note=_clean_optional(body.operator_note),
        source_metadata=body.source_metadata or {},
        content_hash=compute_source_content_hash(
            body.title,
            body.url,
            body.raw_text,
            body.operator_note,
        ),
        status=SourceStatus.PENDING,
        created_by=body.created_by,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return _serialize_source(source)


@router.get("/sources/{source_id}", response_model=SourceAssetResponse)
async def get_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    notes = await _get_notes_for_source(db, source.id)
    return _serialize_source(source, evidence_notes=notes, evidence_note_count=len(notes))


@router.patch("/sources/{source_id}", response_model=SourceAssetResponse)
async def patch_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    body: SourceAssetPatch,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    update = body.model_dump(exclude_unset=True)
    material_fields = {"source_type", "title", "url", "raw_text", "operator_note", "source_metadata"}
    material_changed = bool(material_fields.intersection(update.keys()))

    for field_name, value in update.items():
        if field_name in {"url", "raw_text", "operator_note"}:
            value = _clean_optional(value)
        setattr(source, field_name, value)

    if not ((source.url and source.url.strip()) or (source.raw_text and source.raw_text.strip())):
        raise HTTPException(status_code=400, detail="자료 URL 또는 자료 본문 중 하나는 필수입니다.")

    if material_changed:
        await db.execute(
            delete(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.source_asset_id == source.id
            )
        )
        source.status = SourceStatus.PENDING
        source.process_error = None
        source.processed_at = None
        source.content_hash = compute_source_content_hash(
            source.title,
            source.url,
            source.raw_text,
            source.operator_note,
        )

    await db.commit()
    await db.refresh(source)
    return _serialize_source(source)


@router.post("/sources/{source_id}/process", response_model=SourceAssetResponse)
async def process_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    if source.status == SourceStatus.EXCLUDED:
        raise HTTPException(status_code=400, detail="제외 처리된 자료는 처리할 수 없습니다.")
    if not source.raw_text or not source.raw_text.strip():
        raise HTTPException(status_code=400, detail="자료 본문이 없는 URL 전용 자료는 처리할 수 없습니다.")

    try:
        # 동기 LLM 호출을 워커 스레드로 — 단일 uvicorn worker의 이벤트 루프 블로킹 방지
        # (이 파일의 PDF/DOCX 추출도 동일하게 to_thread 사용).
        payloads = await asyncio.to_thread(process_source_asset, source)
        for payload in payloads:
            if not validate_source_excerpt(source, payload.source_excerpt):
                raise ValueError(f"source_excerpt가 원문에 존재하지 않습니다: {payload.source_excerpt[:80]}")

        await db.execute(
            delete(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.source_asset_id == source.id
            )
        )
        notes = [
            HospitalSourceEvidenceNote(
                hospital_id=hospital_id,
                source_asset_id=source.id,
                note_type=payload.note_type,
                claim=payload.claim,
                source_excerpt=payload.source_excerpt,
                excerpt_start=payload.excerpt_start,
                excerpt_end=payload.excerpt_end,
                confidence=payload.confidence,
                note_metadata=payload.note_metadata,
            )
            for payload in payloads
        ]
        db.add_all(notes)
        source.status = SourceStatus.PROCESSED
        source.process_error = None
        source.processed_at = datetime.now(timezone.utc)
        source.content_hash = compute_source_content_hash(
            source.title,
            source.url,
            source.raw_text,
            source.operator_note,
        )
        await db.commit()
        await db.refresh(source)
        return _serialize_source(source, evidence_notes=notes, evidence_note_count=len(notes))
    except ValueError as exc:
        source.status = SourceStatus.ERROR
        source.process_error = str(exc)
        await db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sources/{source_id}/exclude", response_model=SourceAssetResponse)
async def exclude_source(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    source = await _get_source_or_404(db, hospital_id, source_id)
    previous_status = source.status
    was_public_photo = source.source_type in PHOTO_SOURCE_TYPES and bool(source.is_public)
    hospital = await _get_hospital_or_404(db, hospital_id) if was_public_photo else None
    if hospital and _has_public_site(hospital):
        ensure_site_revalidate_configured()
    source.status = SourceStatus.EXCLUDED
    source.is_public = False
    await write_audit_log(
        db,
        action="exclude_source_asset",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="source_asset",
        target_id=source.id,
        detail={"from_status": str(previous_status), "source_type": str(source.source_type)},
    )
    await db.commit()
    await db.refresh(source)
    if hospital and _has_public_site(hospital):
        # 커밋 이후이므로 실패해도 raise하지 않는다 (R4).
        await trigger_hospital_site_revalidate_safe(hospital.slug, hospital_name=hospital.name)
    notes = await _get_notes_for_source(db, source.id)
    return _serialize_source(source, evidence_notes=notes, evidence_note_count=len(notes))


@router.post("/sources/upload", status_code=status.HTTP_201_CREATED, response_model=SourceAssetResponse)
async def upload_source_file(
    hospital_id: uuid.UUID,
    source_type: SourceType = Form(...),
    title: str = Form(..., min_length=1, max_length=300),
    file: UploadFile = File(...),
    operator_note: str | None = Form(default=None),
    created_by: str | None = Form(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """이미지/PDF/DOCX 업로드. 사진은 file_url만 저장, 텍스트형 자료는 raw_text 자동 추출."""
    await _get_hospital_or_404(db, hospital_id)

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"파일 크기는 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 이하여야 합니다.")
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    mime_type = file.content_type or ""
    extractor_kind = detect_extractor_for(mime_type, file.filename or "")
    is_photo_type = source_type in PHOTO_SOURCE_TYPES

    if is_photo_type and extractor_kind != "IMAGE":
        raise HTTPException(status_code=400, detail="사진 카테고리에는 이미지 파일만 업로드할 수 있습니다.")
    if not is_photo_type and extractor_kind == "IMAGE":
        raise HTTPException(status_code=400, detail="이미지를 업로드하려면 사진 카테고리(PHOTO_*)를 선택해 주세요.")

    # 동기 GCS 업로드(최대 12MB)와 PDF/DOCX 파싱은 이벤트 루프를 수 초 블로킹할 수 있다 —
    # 워커 스레드에서 실행해 공개 표면 요청이 함께 멈추지 않게 한다.
    file_url = await asyncio.to_thread(
        store_asset_bytes,
        hospital_id=hospital_id,
        filename=file.filename or "asset",
        data=data,
        mime_type=mime_type or "application/octet-stream",
    )

    raw_text: str | None = None
    if extractor_kind == "PDF":
        raw_text = await asyncio.to_thread(extract_pdf_text, data) or None
    elif extractor_kind == "DOCX":
        raw_text = await asyncio.to_thread(extract_docx_text, data) or None

    source = HospitalSourceAsset(
        hospital_id=hospital_id,
        source_type=source_type,
        title=title.strip(),
        url=None,
        raw_text=raw_text,
        operator_note=_clean_optional(operator_note),
        source_metadata={"original_filename": file.filename or ""},
        file_url=file_url,
        mime_type=mime_type or None,
        file_size_bytes=len(data),
        is_public=False,
        content_hash=compute_source_content_hash(title, None, raw_text, operator_note),
        status=SourceStatus.PENDING,
        created_by=created_by,
    )
    db.add(source)
    await write_audit_log(
        db,
        action="upload_source_asset",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="source_asset",
        target_id=source.id,
        detail={"source_type": source_type.value, "extractor": extractor_kind, "size_bytes": len(data)},
    )
    await db.commit()
    await db.refresh(source)
    return _serialize_source(source)


@router.post("/sources/crawl", status_code=status.HTTP_201_CREATED, response_model=SourceAssetResponse)
async def crawl_source_url(
    hospital_id: uuid.UUID,
    body: SourceCrawlRequest,
    db: AsyncSession = Depends(get_db),
):
    """URL을 자동 fetch + html2text → raw_text 채움 후 source 생성."""
    await _get_hospital_or_404(db, hospital_id)

    if body.source_type in PHOTO_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="사진 카테고리는 URL 크롤링을 지원하지 않습니다. 업로드를 사용해 주세요.")

    text, error, quality = await fetch_url_text(body.url)
    if error:
        raise HTTPException(status_code=400, detail=f"URL 크롤링 실패: {error}")
    # 네이버 등에서 본문 대신 빈 프레임셋 셸만 받아온 경우 — junk 저장 대신 명확히 거부한다.
    if quality is not None and quality.looks_like_shell:
        if body.source_type == SourceType.NAVER_BLOG:
            raise HTTPException(
                status_code=400,
                detail="네이버 블로그 본문을 가져오지 못했습니다 — 본문을 직접 붙여넣어 주세요.",
            )
        raise HTTPException(
            status_code=400,
            detail="페이지 본문을 충분히 가져오지 못했습니다 — 본문을 직접 붙여넣어 주세요.",
        )

    source = HospitalSourceAsset(
        hospital_id=hospital_id,
        source_type=body.source_type,
        title=body.title.strip(),
        url=body.url.strip(),
        raw_text=text or None,
        operator_note=_clean_optional(body.operator_note),
        source_metadata={"crawled_at": datetime.now(timezone.utc).isoformat()},
        content_hash=compute_source_content_hash(body.title, body.url, text, body.operator_note),
        status=SourceStatus.PENDING,
        created_by=body.created_by,
    )
    db.add(source)
    await write_audit_log(
        db,
        action="crawl_source_url",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="source_asset",
        target_id=source.id,
        detail={"source_type": body.source_type.value, "url": body.url, "extracted_chars": len(text)},
    )
    await db.commit()
    await db.refresh(source)
    return _serialize_source(source)


@router.post("/sources/crawl-blog", status_code=status.HTTP_200_OK, response_model=BlogCrawlResult)
async def crawl_naver_blog(
    hospital_id: uuid.UUID,
    body: BlogCrawlRequest,
    db: AsyncSession = Depends(get_db),
):
    """네이버 블로그 RSS로 최근 글을 일괄 수집해 source로 저장한다.

    각 글은 모바일 본문 URL로 정규화되어 fetch되고, 빈 셸·중복(url/content_hash)은 건너뛴다.
    동기 처리라 max_posts는 요청 타임아웃을 고려해 15개로 제한한다.
    """
    await _get_hospital_or_404(db, hospital_id)

    post_urls, enum_error = await fetch_naver_blog_post_urls(body.url, body.max_posts)
    if enum_error:
        raise HTTPException(status_code=400, detail=f"네이버 블로그 글 목록을 가져오지 못했습니다: {enum_error}")

    blog_id = naver_blog_id_from(body.url)

    existing_rows = await db.execute(
        select(HospitalSourceAsset.url, HospitalSourceAsset.content_hash).where(
            HospitalSourceAsset.hospital_id == hospital_id
        )
    )
    existing_urls: set[str] = set()
    existing_hashes: set[str] = set()
    for row_url, row_hash in existing_rows.all():
        if row_url:
            existing_urls.add(row_url)
        if row_hash:
            existing_hashes.add(row_hash)

    created = 0
    skipped_duplicate = 0
    skipped_empty = 0
    failed: list[dict[str, str]] = []
    source_ids: list[str] = []

    for index, post_url in enumerate(post_urls, start=1):
        if post_url in existing_urls:
            skipped_duplicate += 1
            continue
        text, error, quality = await fetch_url_text(post_url)
        if error:
            failed.append({"url": post_url, "reason": error})
            continue
        if (quality is not None and quality.looks_like_shell) or not (text and text.strip()):
            skipped_empty += 1
            continue
        title = f"네이버 블로그 {blog_id} #{index}"
        content_hash = compute_source_content_hash(title, post_url, text, body.operator_note)
        if content_hash in existing_hashes:
            skipped_duplicate += 1
            continue
        source = HospitalSourceAsset(
            hospital_id=hospital_id,
            source_type=SourceType.NAVER_BLOG,
            title=title,
            url=post_url,
            raw_text=text,
            operator_note=_clean_optional(body.operator_note),
            source_metadata={
                "crawled_at": datetime.now(timezone.utc).isoformat(),
                "bulk_blog_id": blog_id,
            },
            content_hash=content_hash,
            status=SourceStatus.PENDING,
            created_by=body.created_by,
        )
        db.add(source)
        await db.flush()
        existing_urls.add(post_url)
        existing_hashes.add(content_hash)
        source_ids.append(str(source.id))
        created += 1

    if created:
        await write_audit_log(
            db,
            action="crawl_naver_blog",
            hospital_id=hospital_id,
            actor=default_actor(),
            target_type="hospital",
            target_id=hospital_id,
            detail={"blog_id": blog_id, "created": created, "requested": len(post_urls)},
        )
        await db.commit()

    return BlogCrawlResult(
        blog_id=blog_id,
        requested=len(post_urls),
        created=created,
        skipped_duplicate=skipped_duplicate,
        skipped_empty=skipped_empty,
        failed=failed,
        source_ids=source_ids,
    )


@router.patch("/sources/{source_id}/public", response_model=SourceAssetResponse)
async def toggle_source_public(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    body: SourcePublicToggle,
    db: AsyncSession = Depends(get_db),
):
    """사진 자료의 /site 공개 노출 플래그 토글. 사진이 아닌 자료는 거부."""
    source = await _get_source_or_404(db, hospital_id, source_id)
    if source.source_type not in PHOTO_SOURCE_TYPES:
        raise HTTPException(status_code=400, detail="공개 토글은 사진 자료(PHOTO_*)에만 적용됩니다.")
    if body.is_public and source.status == SourceStatus.EXCLUDED:
        raise HTTPException(status_code=400, detail="제외 처리된 사진은 공개할 수 없습니다.")
    if body.is_public and not source.file_url:
        raise HTTPException(status_code=400, detail="파일이 없는 사진은 공개할 수 없습니다.")
    previous = bool(source.is_public)
    hospital = await _get_hospital_or_404(db, hospital_id)
    will_change_public_photo = previous != bool(body.is_public)
    if will_change_public_photo and _has_public_site(hospital):
        ensure_site_revalidate_configured()
    source.is_public = bool(body.is_public)
    await write_audit_log(
        db,
        action="toggle_source_public",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="source_asset",
        target_id=source.id,
        detail={"from": previous, "to": bool(body.is_public), "source_type": source.source_type.value},
    )
    await db.commit()
    await db.refresh(source)
    if will_change_public_photo and _has_public_site(hospital):
        # 커밋 이후이므로 실패해도 raise하지 않는다 (R4).
        await trigger_hospital_site_revalidate_safe(hospital.slug, hospital_name=hospital.name)
    notes = await _get_notes_for_source(db, source.id)
    return _serialize_source(source, evidence_notes=notes, evidence_note_count=len(notes))


@router.get("/sources/{source_id}/file")
async def get_source_file(
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Admin-only source file access. Public access goes through the public asset gate."""
    source = await _get_source_or_404(db, hospital_id, source_id)
    if not source.file_url:
        raise HTTPException(status_code=404, detail="Source file not found")
    return _asset_response(source.file_url, hospital_id=hospital_id, media_type=source.mime_type)


@router.get("/philosophies", response_model=list[PhilosophyResponse])
async def list_philosophies(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await _get_hospital_or_404(db, hospital_id)
    result = await db.execute(
        select(HospitalContentPhilosophy)
        .where(HospitalContentPhilosophy.hospital_id == hospital_id)
        .order_by(HospitalContentPhilosophy.version.desc())
    )
    return [_serialize_philosophy(item) for item in result.scalars().all()]


@router.get("/philosophy/approved", response_model=ApprovedPhilosophyResponse)
async def get_approved_philosophy(hospital_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await _get_hospital_or_404(db, hospital_id)
    approved = await _get_approved(db, hospital_id)
    return {"approved": _serialize_philosophy(approved) if approved else None}


@router.post("/philosophy/draft", status_code=status.HTTP_201_CREATED, response_model=PhilosophyResponse)
async def create_philosophy_draft(
    hospital_id: uuid.UUID,
    body: PhilosophyDraftCreate,
    db: AsyncSession = Depends(get_db),
):
    hospital = await _get_hospital_or_404(db, hospital_id)
    sources = await _select_processed_sources(db, hospital_id, body.source_asset_ids)
    if not sources:
        raise HTTPException(status_code=400, detail="처리된 병원 자료가 1개 이상 필요합니다.")

    notes = await _get_notes_for_sources(db, [source.id for source in sources])
    if not notes:
        raise HTTPException(status_code=400, detail="운영 기준 초안 생성에 사용할 근거 노트가 없습니다.")

    payload = synthesize_philosophy(hospital, sources, notes, operator_note=body.operator_note)
    # 차단·오류 페이지 잔재가 핵심 필드에 남았으면 초안을 만들지 않고 명확한 사유로 거부한다.
    marker_fields = find_error_marker_fields(payload)
    if marker_fields:
        raise HTTPException(
            status_code=422,
            detail={
                "error_markers": marker_fields,
                "reason": (
                    "차단·오류 페이지 잔재가 포함되어 콘텐츠 운영 기준 초안을 생성하지 않았습니다. "
                    "해당 자료를 제외하거나 본문을 다시 수집한 뒤 시도하세요."
                ),
            },
        )
    grounding_errors = validate_philosophy_grounding(payload, notes)
    if grounding_errors:
        raise HTTPException(status_code=422, detail={"grounding_errors": grounding_errors})

    version = await _next_version(db, hospital_id)
    philosophy = HospitalContentPhilosophy(
        hospital_id=hospital_id,
        version=version,
        status=PhilosophyStatus.DRAFT,
        created_by=body.created_by,
        **payload,
    )
    db.add(philosophy)
    await db.commit()
    await db.refresh(philosophy)
    return _serialize_philosophy(philosophy)


@router.patch("/philosophy/{philosophy_id}", response_model=PhilosophyResponse)
async def patch_philosophy(
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
    body: PhilosophyPatch,
    db: AsyncSession = Depends(get_db),
):
    philosophy = await _get_philosophy_or_404(db, hospital_id, philosophy_id)
    if philosophy.status != PhilosophyStatus.DRAFT:
        raise HTTPException(status_code=400, detail="승인 또는 보관된 콘텐츠 운영 기준은 직접 수정할 수 없습니다.")

    update = body.model_dump(exclude_unset=True)
    for field_name, value in update.items():
        setattr(philosophy, field_name, value)

    if _touches_source_backed_fields(update):
        notes = await _get_notes_for_philosophy(db, philosophy)
        grounding_errors = validate_philosophy_grounding(
            philosophy,
            notes,
            require_text_support=True,
        )
        if grounding_errors:
            raise HTTPException(status_code=422, detail={"grounding_errors": grounding_errors})

    await db.commit()
    await db.refresh(philosophy)
    return _serialize_philosophy(philosophy)


@router.post("/philosophy/{philosophy_id}/approve", response_model=PhilosophyResponse)
async def approve_philosophy(
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
    body: PhilosophyApprove,
    db: AsyncSession = Depends(get_db),
):
    philosophy = await _get_philosophy_or_404(db, hospital_id, philosophy_id)
    if philosophy.status != PhilosophyStatus.DRAFT:
        raise HTTPException(status_code=400, detail="초안 상태의 콘텐츠 운영 기준만 승인할 수 있습니다.")
    if not body.confirm_evidence_reviewed:
        raise HTTPException(
            status_code=400,
            detail="검토된 병원 자료와 근거 노트를 확인해야 콘텐츠 운영 기준을 승인할 수 있습니다.",
        )

    notes = await _get_notes_for_philosophy(db, philosophy)
    grounding_errors = validate_philosophy_grounding(philosophy, notes, require_text_support=True)
    if grounding_errors:
        raise HTTPException(status_code=422, detail={"grounding_errors": grounding_errors})

    previous_result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    for previous in previous_result.scalars().all():
        if previous.id != philosophy.id:
            previous.status = PhilosophyStatus.ARCHIVED
    await db.flush()

    philosophy.status = PhilosophyStatus.APPROVED
    philosophy.reviewed_by = body.reviewed_by
    philosophy.approval_note = body.approval_note
    philosophy.approved_at = datetime.now(timezone.utc)
    await write_audit_log(
        db,
        action="approve_philosophy",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="philosophy",
        target_id=philosophy.id,
        detail={
            "version": philosophy.version,
            "claimed_reviewer": body.reviewed_by,
            "evidence_reviewed_confirmed": True,
            "approval_note": body.approval_note,
            "source_asset_count": len(philosophy.source_asset_ids or []),
        },
    )
    await db.commit()
    await db.refresh(philosophy)
    return _serialize_philosophy(philosophy)


async def _get_hospital_or_404(db: AsyncSession, hospital_id: uuid.UUID) -> Hospital:
    hospital = await db.get(Hospital, hospital_id)
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital


def _has_public_site(hospital: Hospital) -> bool:
    return hospital.status == HospitalStatus.ACTIVE and bool(hospital.site_live)


async def _get_source_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    source_id: uuid.UUID,
) -> HospitalSourceAsset:
    source = await db.get(HospitalSourceAsset, source_id)
    if not source or source.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


async def _get_philosophy_or_404(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    philosophy_id: uuid.UUID,
) -> HospitalContentPhilosophy:
    philosophy = await db.get(HospitalContentPhilosophy, philosophy_id)
    if not philosophy or philosophy.hospital_id != hospital_id:
        raise HTTPException(status_code=404, detail="Philosophy not found")
    return philosophy


async def _get_approved(db: AsyncSession, hospital_id: uuid.UUID) -> HospitalContentPhilosophy | None:
    result = await db.execute(
        select(HospitalContentPhilosophy).where(
            HospitalContentPhilosophy.hospital_id == hospital_id,
            HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED,
        )
    )
    return result.scalar_one_or_none()


async def _note_counts(db: AsyncSession, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not source_ids:
        return {}
    result = await db.execute(
        select(HospitalSourceEvidenceNote.source_asset_id, func.count())
        .where(HospitalSourceEvidenceNote.source_asset_id.in_(source_ids))
        .group_by(HospitalSourceEvidenceNote.source_asset_id)
    )
    return {source_id: int(count) for source_id, count in result.all()}


async def _get_notes_for_source(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> list[HospitalSourceEvidenceNote]:
    result = await db.execute(
        select(HospitalSourceEvidenceNote)
        .where(HospitalSourceEvidenceNote.source_asset_id == source_id)
        .order_by(HospitalSourceEvidenceNote.created_at.asc())
    )
    return result.scalars().all()


async def _get_notes_for_sources(
    db: AsyncSession,
    source_ids: list[uuid.UUID],
) -> list[HospitalSourceEvidenceNote]:
    result = await db.execute(
        select(HospitalSourceEvidenceNote)
        .where(HospitalSourceEvidenceNote.source_asset_id.in_(source_ids))
        .order_by(HospitalSourceEvidenceNote.created_at.asc())
    )
    return result.scalars().all()


async def _get_notes_for_philosophy(
    db: AsyncSession,
    philosophy: HospitalContentPhilosophy,
) -> list[HospitalSourceEvidenceNote]:
    source_ids = [uuid.UUID(str(source_id)) for source_id in (philosophy.source_asset_ids or [])]
    if not source_ids:
        result = await db.execute(
            select(HospitalSourceEvidenceNote).where(
                HospitalSourceEvidenceNote.hospital_id == philosophy.hospital_id
            )
        )
        return result.scalars().all()
    return await _get_notes_for_sources(db, source_ids)


async def _select_processed_sources(
    db: AsyncSession,
    hospital_id: uuid.UUID,
    source_asset_ids: list[str] | None,
) -> list[HospitalSourceAsset]:
    stmt = select(HospitalSourceAsset).where(
        HospitalSourceAsset.hospital_id == hospital_id,
        HospitalSourceAsset.status == SourceStatus.PROCESSED,
    )
    if source_asset_ids:
        try:
            ids = [uuid.UUID(str(item)) for item in source_asset_ids]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="선택한 병원 자료 ID 형식이 올바르지 않습니다.") from exc
        stmt = stmt.where(HospitalSourceAsset.id.in_(ids))
    stmt = stmt.order_by(HospitalSourceAsset.processed_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


async def _next_version(db: AsyncSession, hospital_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.max(HospitalContentPhilosophy.version)).where(
            HospitalContentPhilosophy.hospital_id == hospital_id
        )
    )
    return int(result.scalar_one() or 0) + 1


def _serialize_source(
    source: HospitalSourceAsset,
    evidence_note_count: int = 0,
    evidence_notes: list[HospitalSourceEvidenceNote] | None = None,
) -> dict:
    return {
        "id": str(source.id),
        "hospital_id": str(source.hospital_id),
        "source_type": source.source_type,
        "display": _serialize_source_display(source),
        "title": source.title,
        "url": source.url,
        "raw_text": source.raw_text,
        "operator_note": source.operator_note,
        "source_metadata": source.source_metadata or {},
        "content_hash": source.content_hash,
        "status": source.status,
        "process_error": source.process_error,
        "processed_at": source.processed_at.isoformat() if source.processed_at else None,
        "created_by": source.created_by,
        "updated_by": source.updated_by,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
        "file_url": source.file_url if _is_legacy_public_url(source.file_url) else None,
        "file_access_url": _source_file_access_url(source) if source.file_url else None,
        "mime_type": source.mime_type,
        "file_size_bytes": source.file_size_bytes,
        "is_public": bool(source.is_public),
        "evidence_note_count": evidence_note_count,
        "evidence_notes": [_serialize_note(note) for note in evidence_notes] if evidence_notes is not None else None,
    }


def _source_file_access_url(source: HospitalSourceAsset) -> str:
    return f"/api/admin/hospitals/{source.hospital_id}/essence/sources/{source.id}/file"


def _is_legacy_public_url(value: str | None) -> bool:
    return bool(value and (value.startswith("http://") or value.startswith("https://") or value.startswith("/assets/")))


def _asset_response(asset_ref: str, *, hospital_id: uuid.UUID, media_type: str | None):
    if asset_ref.startswith("local://"):
        path = resolve_local_asset_path(asset_ref, expected_hospital_id=hospital_id)
        if not path or not path.exists():
            raise HTTPException(status_code=404, detail="Source file not found")
        return FileResponse(path, media_type=media_type)
    if asset_ref.startswith("gs://"):
        signed_url = get_signed_url(asset_ref)
        if not signed_url or signed_url == asset_ref:
            raise HTTPException(status_code=503, detail="Could not create signed asset URL")
        return RedirectResponse(url=signed_url, status_code=302)
    if asset_ref.startswith("/assets/"):
        path = resolve_legacy_asset_path(asset_ref, expected_hospital_id=hospital_id)
        if path and path.exists():
            return FileResponse(path, media_type=media_type)
        raise HTTPException(status_code=404, detail="Source file not found")
    if asset_ref.startswith("http://") or asset_ref.startswith("https://"):
        return RedirectResponse(url=asset_ref, status_code=302)
    raise HTTPException(status_code=404, detail="Source file not found")


def _serialize_source_display(source: HospitalSourceAsset) -> dict:
    return {
        "source_type_label": _display_label(SOURCE_TYPE_DISPLAY_LABELS, source.source_type),
        "status_label": _display_label(SOURCE_STATUS_DISPLAY_LABELS, source.status),
    }


def _serialize_philosophy_display(philosophy: HospitalContentPhilosophy) -> dict:
    return {
        "status_label": _display_label(PHILOSOPHY_STATUS_DISPLAY_LABELS, philosophy.status),
    }


def _serialize_note(note: HospitalSourceEvidenceNote) -> dict:
    return {
        "id": str(note.id),
        "hospital_id": str(note.hospital_id),
        "source_asset_id": str(note.source_asset_id),
        "note_type": note.note_type,
        "claim": note.claim,
        "source_excerpt": note.source_excerpt,
        "excerpt_start": note.excerpt_start,
        "excerpt_end": note.excerpt_end,
        "confidence": note.confidence,
        "note_metadata": note.note_metadata or {},
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


def _serialize_philosophy(philosophy: HospitalContentPhilosophy) -> dict:
    return {
        "id": str(philosophy.id),
        "hospital_id": str(philosophy.hospital_id),
        "version": philosophy.version,
        "status": philosophy.status,
        "display": _serialize_philosophy_display(philosophy),
        "positioning_statement": philosophy.positioning_statement,
        "doctor_voice": philosophy.doctor_voice,
        "patient_promise": philosophy.patient_promise,
        "content_principles": philosophy.content_principles or [],
        "tone_guidelines": philosophy.tone_guidelines or [],
        "must_use_messages": philosophy.must_use_messages or [],
        "avoid_messages": philosophy.avoid_messages or [],
        "treatment_narratives": philosophy.treatment_narratives or [],
        "local_context": philosophy.local_context or {},
        "medical_ad_risk_rules": philosophy.medical_ad_risk_rules or [],
        "evidence_map": philosophy.evidence_map or {},
        "source_asset_ids": philosophy.source_asset_ids or [],
        "unsupported_gaps": philosophy.unsupported_gaps or [],
        "conflict_notes": philosophy.conflict_notes or [],
        "synthesis_notes": philosophy.synthesis_notes,
        "source_snapshot_hash": philosophy.source_snapshot_hash,
        "created_by": philosophy.created_by,
        "reviewed_by": philosophy.reviewed_by,
        "approved_at": philosophy.approved_at.isoformat() if philosophy.approved_at else None,
        "approval_note": philosophy.approval_note,
        "created_at": philosophy.created_at.isoformat() if philosophy.created_at else None,
        "updated_at": philosophy.updated_at.isoformat() if philosophy.updated_at else None,
    }


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _touches_source_backed_fields(update: dict) -> bool:
    source_backed_fields = {
        "positioning_statement",
        "doctor_voice",
        "patient_promise",
        "content_principles",
        "tone_guidelines",
        "must_use_messages",
        "avoid_messages",
        "treatment_narratives",
        "local_context",
        "medical_ad_risk_rules",
        "evidence_map",
    }
    return bool(source_backed_fields.intersection(update.keys()))
