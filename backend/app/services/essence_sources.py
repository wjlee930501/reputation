"""URL 자료 등록과, 콘텐츠 운영 기준의 "필수 텍스트 자료" 경계.

제외되지 않은, 사진이 아닌, **원문이 있는** 자료만 필수다. URL만 있고 본문이 없는 자료는 추출할
것이 없으므로 처리도 승인도 막지 않는다(M-01). 크롤·업로드로 본문이 생기는 순간 PENDING 필수 자료가
되어 승인이 stale이 된다. `required_text_source_predicate`는 readiness·자동 검수·승인·노이즈 hash가
모두 같은 경계를 쓰도록 여기 한 곳에 둔다.

URL 등록은 두 갈래다. 자료 화면의 크롤 라우트는 사람이 결과를 기다리므로 `register_url_source`가
그 자리에서 fetch까지 한다. 기본 정보 저장은 사람을 기다리게 하지 않는다 — 본문 없는 PENDING 행만
만들고(`create_pending_channel_source`) fetch는 워커(`fetch_channel_source`)가 한다. 두 경로 모두
`fetch_source_content` 하나를 쓰므로 검증 규칙은 한 벌이다. 라우트 모듈은 절대 import하지 않는다 —
`app.services.essence_engine`이 이 모듈을 가져가므로 그쪽도 함수 안에서만 늦게 import한다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.celery_app import celery_app
from app.models.essence import (
    PHOTO_SOURCE_TYPES,
    HospitalSourceAsset,
    SourceStatus,
    SourceType,
)
from app.models.operations import OperationRun
from app.services.asset_extractor import fetch_url_text
from app.services.audit_log import default_actor, write_audit_log
from app.services.source_processing_runs import (
    create_or_get_source_processing_run,
    normalized_optional_text,
    prepare_next_source_run_item,
    processing_input_hash,
    release_source_run_dispatch,
)
from app.utils.db_locks import acquire_hospital_advisory_lock
from app.workers.dispatch_auth import build_dispatch_headers

logger = logging.getLogger(__name__)

# btrim은 인자가 하나면 공백만 깎는다. 줄바꿈·탭만 있는 본문도 "없음"으로 봐야 하므로
# Python의 str.strip()과 같은 공백 집합을 그대로 적는다. 워커는 .strip()이 빈 문자열이면
# 처리를 거부하므로, 여기서 집합이 좁으면 NBSP·전각 공백만 있는 본문이 "필수인데 처리 불가"가 된다.
_BLANK_CHARS = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def required_text_source_predicate() -> ColumnElement[bool]:
    return and_(
        HospitalSourceAsset.status != SourceStatus.EXCLUDED,
        HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
        HospitalSourceAsset.raw_text.isnot(None),
        func.length(func.btrim(HospitalSourceAsset.raw_text, _BLANK_CHARS)) > 0,
    )


class SourceRegistrationError(Exception):
    """URL 자료 등록 실패. 메시지는 그대로 운영자에게 보여 줄 수 있는 문장만 담는다.

    라우트는 `status_code`를 그대로 HTTP 코드로 되돌려 준다 — 기존 크롤 API의 400/422
    구분을 서비스로 옮기면서 잃지 않기 위한 값이다.
    """

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


#: 채널 주소 행의 fetch 진행 상태. `source_metadata["fetch_state"]`에 그대로 들어간다.
FETCH_STATE_QUEUED = "QUEUED"
FETCH_STATE_FETCHED = "FETCHED"
FETCH_STATE_FAILED = "FAILED"
#: 워커가 같은 주소를 다시 받아오는 횟수 상한. 넘으면 ERROR + 운영 예외 1건이다.
CHANNEL_FETCH_ATTEMPT_BUDGET = 3


def normalize_source_url(url: str) -> str:
    """중복 판정용 정규화. 표시·저장은 사람이 입력한 원문을 그대로 쓴다.

    같은 페이지를 가리키는 `http://WWW.a.com/`과 `https://a.com`이 서로 다른 자료로 두 번
    등록되면, 실패한 등록이 저장할 때마다 새 행을 만든다.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if not parsed.netloc:
        return cleaned.rstrip("/").lower()
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    return urlunparse(
        (parsed.scheme.lower(), host, path, parsed.params, parsed.query, "")
    )


def is_youtube_channel_home(url: str) -> bool:
    """Channel listing pages have almost no article body and must not become evidence."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {"youtu.be", "www.youtu.be"}:
        return False
    if host not in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        return False
    path = parsed.path or ""
    if (
        path.startswith("/watch")
        or path.startswith("/shorts/")
        or path.startswith("/embed/")
        or path.startswith("/live/")
    ):
        return False
    if "v=" in (parsed.query or ""):
        return False
    return (
        path.startswith("/@")
        or path.startswith("/channel/")
        or path.startswith("/c/")
        or path.startswith("/user/")
    )


async def dispatch_source_processing_run_best_effort(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    prepared = await prepare_next_source_run_item(db, run_id)
    if prepared is None:
        return False
    _run, source_id, dispatch_token = prepared
    try:
        celery_app.send_task(
            "app.workers.tasks.process_source_asset_task",
            args=[source_id, str(run_id), dispatch_token],
            queue="default",
            headers=build_dispatch_headers(
                "app.workers.tasks.process_source_asset_task", source_id
            ),
        )
        return True
    except Exception as exc:
        await release_source_run_dispatch(
            db,
            run_id,
            source_id=source_id,
            dispatch_token=dispatch_token,
            error=str(exc),
        )
        logger.exception("Failed to dispatch source-processing run %s", run_id)
        return False


async def start_source_processing_best_effort(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    source_identities: list[str] | None = None,
) -> OperationRun | None:
    if not source_ids:
        return None
    # essence_engine은 이 모듈을 import한다 — 모듈 최상단에서 되가져오면 순환 import다.
    from app.services.essence_engine import compute_source_content_hash

    # Serialize only durable run creation. Provider work happens later in the
    # worker after this transaction has committed and released the hospital lock.
    await acquire_hospital_advisory_lock(db, hospital_id)
    if source_identities is None:
        rows = list(
            (
                await db.execute(
                    select(HospitalSourceAsset).where(HospitalSourceAsset.id.in_(source_ids))
                )
            )
            .scalars()
            .all()
        )
        source_identities = [
            f"{source.id}:{processing_input_hash(source, compute_source_content_hash(source.title, source.url, source.raw_text, source.operator_note))}"
            for source in rows
        ]
    run, _created = await create_or_get_source_processing_run(
        db,
        hospital_id=hospital_id,
        source_ids=source_ids,
        source_identities=source_identities,
    )
    await dispatch_source_processing_run_best_effort(db, run.id)
    await db.refresh(run)
    return run


@dataclass(frozen=True)
class FetchedSourceContent:
    """fetch 결과 중 자료 행에 그대로 들어가는 두 값."""

    title: str
    text: str


class TransientSourceFetchError(Exception):
    """네트워크·타임아웃처럼 다시 시도하면 달라질 수 있는 실패. 재시도 예산을 쓴다."""


async def fetch_source_content(
    *,
    source_type: SourceType,
    url: str,
    title: str | None = None,
) -> FetchedSourceContent:
    """URL을 fetch해 근거로 쓸 제목과 본문을 만든다.

    영구 실패(사진 유형·채널 홈·셸 페이지·제목 없음)는 `SourceRegistrationError`로,
    다시 시도할 값이 있는 실패는 `TransientSourceFetchError`로 구분해 알린다. 호출자가
    HTTP로 옮기든 워커 재시도로 옮기든 같은 문장을 쓴다.
    """
    if source_type in PHOTO_SOURCE_TYPES:
        raise SourceRegistrationError(
            "사진 카테고리는 URL 크롤링을 지원하지 않습니다. 업로드를 사용해 주세요."
        )
    if is_youtube_channel_home(url):
        raise SourceRegistrationError(
            "유튜브 채널 홈은 본문이 없어 근거로 쓰지 않습니다. 개별 영상 URL을 넣어 주세요.",
            status_code=422,
        )

    text, error, quality = await fetch_url_text(url)
    if error:
        raise TransientSourceFetchError(f"URL 크롤링 실패: {error}")
    # 네이버 등에서 본문 대신 빈 프레임셋 셸만 받아온 경우 — junk 저장 대신 명확히 거부한다.
    if quality is not None and quality.looks_like_shell:
        if source_type == SourceType.NAVER_BLOG:
            raise SourceRegistrationError(
                "네이버 블로그 본문을 가져오지 못했습니다 — 본문을 직접 붙여넣어 주세요."
            )
        raise SourceRegistrationError(
            "페이지 본문을 충분히 가져오지 못했습니다 — 본문을 직접 붙여넣어 주세요."
        )

    final_title = (title or "").strip() or (quality.page_title if quality else None)
    if not final_title:
        raise SourceRegistrationError(
            "페이지 제목을 찾지 못했습니다. 자료 제목을 직접 입력해 주세요.",
            status_code=422,
        )
    return FetchedSourceContent(title=final_title, text=text)


async def find_active_source_id_by_url(
    db: AsyncSession, *, hospital_id: uuid.UUID, url: str
) -> uuid.UUID | None:
    """같은 주소의, 제외되지 않은 자료 id. 없으면 None.

    정규화 비교라 SQL 한 줄로 끝나지 않는다. 병원 한 곳의 자료 수는 화면에 다 그리는
    규모라 Python에서 비교해도 된다.
    """
    target = normalize_source_url(url)
    if not target:
        return None
    rows = (
        await db.execute(
            select(HospitalSourceAsset.id, HospitalSourceAsset.url).where(
                HospitalSourceAsset.hospital_id == hospital_id,
                HospitalSourceAsset.status != SourceStatus.EXCLUDED,
                HospitalSourceAsset.url.isnot(None),
            )
        )
    ).all()
    for source_id, stored_url in rows:
        if normalize_source_url(stored_url or "") == target:
            return source_id
    return None


async def create_pending_channel_source(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    source_type: SourceType,
    url: str,
    title: str,
    channel_field: str,
    created_by: str | None = None,
) -> HospitalSourceAsset:
    """fetch 없이 근거 자료 행만 만든다 — 저장 응답을 기다리게 하지 않기 위해서다.

    본문은 워커(`fetch_channel_source`)가 채운다. 커밋은 호출자가 한다: 프로파일 저장과
    같은 트랜잭션에서 만들어야 "행은 없는데 응답은 등록됐다고 말하는" 상태가 없다.
    """
    source = HospitalSourceAsset(
        hospital_id=hospital_id,
        source_type=source_type,
        title=title,
        url=url.strip(),
        raw_text=None,
        source_metadata={
            "channel_field": channel_field,
            "fetch_state": FETCH_STATE_QUEUED,
            "registered_from": "profile",
            "normalized_url": normalize_source_url(url),
        },
        status=SourceStatus.PENDING,
        created_by=created_by,
    )
    db.add(source)
    await db.flush()
    return source


async def register_url_source(
    db: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    source_type: SourceType,
    url: str,
    title: str | None = None,
    operator_note: str | None = None,
    created_by: str | None = None,
) -> HospitalSourceAsset:
    """URL을 그 자리에서 fetch해 근거 자료로 등록하고 처리까지 이어 준다.

    사람이 결과를 기다리는 크롤 라우트 전용이다. 프로파일 저장은 이 동기 경로를 쓰지
    않는다 — 채널 한 곳에 최대 12초가 걸리기 때문이다.
    """
    from app.services.essence_engine import compute_source_content_hash

    try:
        fetched = await fetch_source_content(source_type=source_type, url=url, title=title)
    except TransientSourceFetchError as exc:
        raise SourceRegistrationError(str(exc)) from exc

    final_title = fetched.title
    text = fetched.text
    crawled_text = normalized_optional_text(text)
    await acquire_hospital_advisory_lock(db, hospital_id)
    source = HospitalSourceAsset(
        hospital_id=hospital_id,
        source_type=source_type,
        title=final_title,
        url=url.strip(),
        raw_text=crawled_text,
        operator_note=normalized_optional_text(operator_note),
        source_metadata={"crawled_at": datetime.now(timezone.utc).isoformat()},
        content_hash=compute_source_content_hash(final_title, url, crawled_text, operator_note),
        status=SourceStatus.PENDING,
        created_by=created_by,
    )
    db.add(source)
    await write_audit_log(
        db,
        action="crawl_source_url",
        hospital_id=hospital_id,
        actor=default_actor(),
        target_type="source_asset",
        target_id=source.id,
        detail={
            "source_type": source_type.value,
            "url": url,
            "extracted_chars": len(text),
        },
    )
    await db.commit()
    await db.refresh(source)
    if source.raw_text and source.raw_text.strip():
        await start_source_processing_best_effort(
            db, hospital_id=hospital_id, source_ids=[source.id]
        )
    return source
