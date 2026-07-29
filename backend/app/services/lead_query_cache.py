"""질의 단위 공유 캐시 (설계 §2-6).

**질의에 병원 이름이 들어가지 않으므로**(PRD F1-1) `수서역 근처 내과 병원 추천해줘`의
AI 답변은 신청 병원이 누구든 동일하다. 병원마다 달라지는 것은 그 답변에서 이 병원
이름이 나왔는지 보는 판정 단계뿐이고, 판정은 콜당 0.26원이다(답변 모델의 1/370).

  첫 번째 병원 (수서역·내과)  1,499원
  같은 질의를 쓰는 두 번째    약 5원   ← 판정 18콜만 다시

캐시 키에 **모델과 프롬프트 버전이 들어간다.** 이것이 빠지면 모델을 바꿔도(luna 이전
같은) 옛 답변이 살아남아, PRD §2-1의 "핀 고정 + 의도적 이전"이 캐시 뒤에서 조용히
깨진다 — 언급률 변화가 플랫폼 탓인지 우리 도구 탓인지 영원히 분리할 수 없게 된다.

**개인정보가 아니다.** 질의에 신청자 이름·연락처를 절대 넣지 않으므로(PRD §6)
파기 파이프라인의 대상이 아니고 TTL로만 관리한다.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.lead_diagnosis import LeadQueryAnswer
from app.services import sov_engine

logger = logging.getLogger(__name__)


def prompt_version() -> str:
    """측정 지시문의 지문.

    수동으로 관리하는 버전 문자열이면 프롬프트를 고치고 버전 올리는 것을 잊는다 —
    그 순간 캐시가 옛 조건의 답변을 새 조건의 결과라고 내놓는다. 프롬프트 자체에서
    파생시켜 잊을 수 없게 만든다.
    """
    return hashlib.sha256(sov_engine.SYSTEM_PROMPT_SOV.encode()).hexdigest()[:16]


def query_cache_key(*, query_text: str, platform: str, requested_model: str) -> str:
    """정규화는 앞뒤 공백과 연속 공백까지만.

    '수서역'과 '수서동'을 같은 키로 묶지 않는다 — 실제로 답변이 다르므로 묶으면
    틀린 숫자를 팔게 된다.
    """
    normalized = " ".join((query_text or "").split())
    material = f"{normalized}|{platform}|{requested_model}|{prompt_version()}"
    return hashlib.sha256(material.encode()).hexdigest()


async def get_cached_answers(
    db: AsyncSession,
    *,
    query_text: str,
    platform: str,
    requested_model: str,
    repeat_count: int,
) -> dict[int, LeadQueryAnswer]:
    """만료되지 않은 답변을 회차별로. 부분 적중이면 부분만 돌려준다.

    반복 3회 중 2회만 캐시에 있으면 나머지 1회만 실제로 호출한다 — 전부 아니면 전무로
    두면 흔한 부분 적중에서 절감이 통째로 사라진다.
    """
    now = datetime.now(timezone.utc)
    key = query_cache_key(
        query_text=query_text, platform=platform, requested_model=requested_model
    )
    rows = (
        await db.execute(
            select(LeadQueryAnswer).where(
                LeadQueryAnswer.query_hash == key,
                LeadQueryAnswer.repeat_no <= repeat_count,
                LeadQueryAnswer.expires_at > now,
            )
        )
    ).scalars().all()
    return {row.repeat_no: row for row in rows}


async def store_answer(
    db: AsyncSession,
    *,
    query_text: str,
    platform: str,
    requested_model: str,
    repeat_no: int,
    answer_model: str | None,
    raw_response: str,
    source_urls: list | None,
    search_calls: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    measured_at: datetime | None = None,
) -> None:
    """성공한 측정만 캐시한다.

    실패 응답을 캐시하면 한 번의 공급자 장애가 7일 동안 모든 신청자에게 전파된다.
    호출부가 SUCCESS만 넘기도록 되어 있지만, 여기서도 빈 응답을 거른다.
    """
    if not (raw_response or "").strip():
        return

    now = measured_at or datetime.now(timezone.utc)
    key = query_cache_key(
        query_text=query_text, platform=platform, requested_model=requested_model
    )
    db.add(
        LeadQueryAnswer(
            query_hash=key,
            repeat_no=repeat_no,
            query_text=query_text[:500],
            platform=platform,
            requested_model=requested_model,
            answer_model=answer_model,
            prompt_version=prompt_version(),
            raw_response=raw_response,
            source_urls=source_urls,
            search_calls=search_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            measured_at=now,
            expires_at=now + timedelta(days=settings.LEADGEN_QUERY_CACHE_TTL_DAYS),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        # 다른 진단이 같은 질의를 동시에 측정해 먼저 넣었다. 캐시는 최적화이지
        # 정합성이 아니므로 경합에서 지는 쪽이 조용히 물러난다.
        await db.rollback()
        logger.debug("lead query answer already cached: %s/%s", key[:12], repeat_no)


async def purge_expired(db: AsyncSession) -> int:
    """만료분 삭제. 되살리지 않는다 — 오래된 답변을 오늘 측정으로 파는 유혹을 없앤다."""
    result = await db.execute(
        delete(LeadQueryAnswer).where(LeadQueryAnswer.expires_at <= datetime.now(timezone.utc))
    )
    return int(result.rowcount or 0)
