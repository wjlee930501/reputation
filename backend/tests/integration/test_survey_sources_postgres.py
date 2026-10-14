"""원장 인터뷰 설문 자료(P4-9)를 실제 PostgreSQL에서 — PDF 업로드·링크·붙여넣기 모두 저장 전에
개인정보를 가리고, 스캔 PDF는 '본문 없음'으로 표시하며, 설문지 링크는 받지 않는다."""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.api.admin import essence as essence_api
from app.models.essence import SourceType
from app.models.hospital import Hospital, HospitalStatus
from app.schemas.essence import SourceAssetCreate
from app.services import essence_sources

SURVEY = (
    "Q. 진료 철학은?\nA. 환자의 이야기를 끝까지 듣습니다. 궁금한 점은 010-9876-5432로 주세요.\n"
    "병원 대표번호 02-123-4567, 진료시간 09:00-18:00"
)


@pytest.fixture
async def hospital(pg_async_session):
    row = Hospital(
        id=uuid.uuid4(),
        name="설문의원",
        slug=f"survey-{uuid.uuid4().hex[:8]}",
        status=HospitalStatus.ONBOARDING,
        region=[],
        specialties=[],
        keywords=[],
        competitors=[],
        treatments=[],
    )
    pg_async_session.add(row)
    await pg_async_session.commit()
    return row


@pytest.fixture
def started(monkeypatch):
    calls = []

    async def fake_start(_db, *, hospital_id, source_ids):
        calls.append(list(source_ids))
        return None

    monkeypatch.setattr(essence_api, "_start_source_processing_best_effort", fake_start)
    monkeypatch.setattr(essence_sources, "start_source_processing_best_effort", fake_start)
    monkeypatch.setattr(essence_api, "store_asset_bytes", lambda **_kw: "local://test/survey.pdf")
    return calls


def _upload(name: str, body: bytes) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(body), filename=name, headers=Headers({"content-type": "application/pdf"})
    )


async def _upload_pdf(db, hospital_id, text):
    return await essence_api.upload_source_file(
        hospital_id,
        source_type=SourceType.INTERVIEW,
        title="",
        file=_upload("원장설문.pdf", b"%PDF-1.7 fake"),
        is_public=None,
        asset_kind=None,
        photo_source_owner=None,
        photo_rights_basis=None,
        photo_evidence_reference=None,
        operator_note=None,
        created_by="ae@example.com",
        skip_revalidate=False,
        db=db,
    )


@pytest.mark.asyncio
async def test_survey_pdf_is_masked_and_processed(pg_async_session, hospital, started, monkeypatch):
    monkeypatch.setattr(essence_api, "extract_pdf_text", lambda _data: SURVEY)

    response = await _upload_pdf(pg_async_session, hospital.id, SURVEY)

    assert response["source_type"] == SourceType.INTERVIEW
    assert response["display"]["source_type_label"] == "원장 인터뷰 설문"
    assert "010-9876-5432" not in response["raw_text"] and "[가림]" in response["raw_text"]
    assert "02-123-4567" in response["raw_text"] and "09:00-18:00" in response["raw_text"]
    assert started == [[uuid.UUID(str(response["id"]))]]


@pytest.mark.asyncio
async def test_scanned_pdf_is_saved_without_text_and_says_so(
    pg_async_session, hospital, started, monkeypatch
):
    monkeypatch.setattr(essence_api, "extract_pdf_text", lambda _data: "   ")

    response = await _upload_pdf(pg_async_session, hospital.id, "")

    assert response["raw_text"] is None
    assert response["source_metadata"]["text_extraction"] == "EMPTY"
    assert "스캔" in response["source_metadata"]["text_extraction_message"]
    assert started == []  # 읽을 본문이 없으면 처리를 시작하지 않는다


@pytest.mark.asyncio
async def test_pasted_survey_text_is_masked(pg_async_session, hospital, started):
    response = await essence_api.create_source(
        hospital.id,
        SourceAssetCreate(source_type=SourceType.INTERVIEW, title="설문", raw_text=SURVEY),
        db=pg_async_session,
    )
    assert "[가림]" in response["raw_text"] and "010-9876-5432" not in response["raw_text"]


@pytest.mark.asyncio
async def test_google_doc_link_is_read_through_its_export_and_masked(
    pg_async_session, hospital, started, monkeypatch
):
    fetched = []

    async def fake_fetch(url):
        fetched.append(url)
        return SURVEY * 3, None, None

    monkeypatch.setattr(essence_sources, "fetch_url_text", fake_fetch)

    response = await essence_api.crawl_source_url(
        hospital.id,
        essence_api.SourceCrawlRequest(
            source_type=SourceType.INTERVIEW,
            url="https://docs.google.com/document/d/1SurveyDoc/edit?usp=sharing",
        ),
        db=pg_async_session,
    )

    assert fetched == ["https://docs.google.com/document/d/1SurveyDoc/export?format=txt"]
    assert response["title"] == "원장 인터뷰 설문"
    assert response["url"] == fetched[0]
    assert response["source_metadata"]["original_url"].endswith("/edit?usp=sharing")
    assert "010-9876-5432" not in response["raw_text"]


@pytest.mark.asyncio
async def test_google_forms_link_is_refused_before_any_fetch(
    pg_async_session, hospital, started, monkeypatch
):
    async def must_not_fetch(_url):
        raise AssertionError("설문지 링크는 읽지 않는다")

    monkeypatch.setattr(essence_sources, "fetch_url_text", must_not_fetch)

    with pytest.raises(HTTPException) as exc:
        await essence_api.crawl_source_url(
            hospital.id,
            essence_api.SourceCrawlRequest(
                source_type=SourceType.INTERVIEW, url="https://forms.gle/abcdef123"
            ),
            db=pg_async_session,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_retyping_to_survey_masks_the_body_a_fetch_just_stored(
    pg_async_session, hospital, started, monkeypatch
):
    """리뷰 재현의 반대 순서: 이 세션이 본문 없는 행을 읽어 둔 뒤 fetch 워커가 본문을 저장했다. 수정은
    잠근 행을 다시 읽어야 방금 저장된 본문까지 가린다(낡은 읽기로는 가릴 본문이 없다)."""

    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.essence import HospitalSourceAsset, SourceStatus
    from app.schemas.essence import SourceAssetPatch

    db = pg_async_session
    source = HospitalSourceAsset(
        hospital_id=hospital.id,
        source_type=SourceType.HOMEPAGE,
        title="홈페이지",
        url="https://clinic.example.com",
        status=SourceStatus.PENDING,
        source_metadata={"fetch_state": "QUEUED"},
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)  # 이 세션은 raw_text=None을 들고 있다
    assert source.raw_text is None

    async with AsyncSession(bind=db.bind, join_transaction_mode="create_savepoint") as worker:
        await worker.execute(
            update(HospitalSourceAsset)
            .where(HospitalSourceAsset.id == source.id)
            .values(raw_text="원장 휴대전화 010-1234-5678")
        )
        await worker.commit()

    response = await essence_api.patch_source(
        hospital.id,
        source.id,
        SourceAssetPatch(source_type=SourceType.INTERVIEW),
        db=db,
    )

    assert response["raw_text"] == "원장 휴대전화 [가림]"
