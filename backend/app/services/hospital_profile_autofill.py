"""병원 프로파일 자동 채우기 — 홈페이지/블로그/네이버 플레이스(+원장 설문) 스크랩 + 구조화 추출.

원칙(불변식):
- 이 모듈은 **초안만 만든다.** 병원 행에 쓰는 일은 `profile_autofill_apply`가 빈 칸·마케터가 손대지
  않은 칸에만 의료광고 필터를 거쳐 한다(D30).
- 모든 소스 수집은 **best-effort**. 일부 소스가 실패해도 가능한 필드만 채운다.
- 필드마다 **출처별 후보**를 받고, 원문 발췌 대조를 통과한 후보 중 고정 우선순위로 고른다(D34) —
  모델이 고르지 않는다. 사실 필드는 네이버 플레이스 > 설문 > 홈페이지 > 블로그, 서술 필드(진료 철학·
  진료 항목 설명)는 설문 > 홈페이지 > 블로그 > 네이버. 값이 다른 후보는 ``conflicts``로만 남긴다.
- 네이버 신원이 확인되지 않으면(E4) 네이버 후보는 순위에서 빠지고, 네이버뿐인 값은 초안에만
  ``verified=False``로 남아 자동 적용되지 않는다.
- 추출 텍스트는 medical_filter로 검사해 위반을 **필드별로 표시**한다(초안은 지우지 않는다 —
  적용 단계가 걸린 필드·원소를 뺀다).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services import llm_structured_output, naver_place, openrouter
from app.services.asset_extractor import fetch_url_text
from app.services.content_engine import _parse_json_response
from app.services.openrouter import is_retryable_llm_error
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)


def _autofill_model() -> str:
    """자동 채우기 모델 — 기본은 빠른 모델, AUTOFILL_MODEL로 되돌릴 수 있다.

    구조화 추출 뒤에 _normalize_fields/_field_value_is_grounded가 필드마다 원문 발췌
    일치를 결정론적으로 검증하므로, 이 단계에서 Sonnet급 추론을 살 이유가 없다.
    """
    return settings.AUTOFILL_MODEL or settings.CLAUDE_MODEL_FAST


_client = openrouter.sync_client(timeout=90.0)  # max_retries=0 — tenacity가 재시도

# 소스별 입력 텍스트 상한 — 토큰/비용 통제.
# 홈페이지·블로그·네이버 플레이스 3개 소스 × 18K자 = 입력 최대 ~54K자(≈20K 토큰).
# 추출 결과는 아래 _field_value_is_grounded가 원문 발췌와 대조해 결정론적으로 검증하므로
# 상한을 더 올려 모델에 원문을 더 넣어도 정확도가 그만큼 오르지 않는다.
_MAX_PER_SOURCE = 18_000

# 추출 결과에서 medical_filter를 적용할 텍스트 필드(광고성·공개 표면 우려).
_FORBIDDEN_TEXT_FIELDS = ("director_career", "director_philosophy")
_FORBIDDEN_LIST_FIELDS = ("keywords",)
# treatments[].description 도 검사 대상(아래 _collect_violations에서 별도 처리).

# Claude가 채울 수 있는 필드(허용 목록). draft는 이 키만 통과시킨다.
_ALLOWED_FIELDS = {
    "director_name",
    "director_career",
    "director_philosophy",
    "address",
    "phone",
    "business_hours",
    "website_url",
    "blog_url",
    "kakao_channel_url",
    "naver_place_url",
    "region",
    "specialties",
    "keywords",
    "competitors",
    "treatments",
}

EXTRACTION_SYSTEM_PROMPT = """\
당신은 병원 공개 정보를 정확히 정리하는 데이터 추출기입니다.
주어진 출처 텍스트(병원 홈페이지/블로그/네이버 플레이스)에서만 사실을 뽑아
아래 JSON 스키마로 반환합니다. 새 사실을 지어내지 마세요.

[엄수 규칙]
1. 출처에 근거가 있는 필드만 채우고, 근거가 없으면 그 필드를 생략(omit)합니다. 추측 금지.
2. 값은 출처 표현을 따르되 간결하게 정리합니다. 과장·홍보 문구는 제거합니다.
3. 의료광고법상 다음 표현은 절대 생성·전재하지 마세요(있어도 빼고 정리):
   1등·최고·최우수·유일·완치·100%·성공률·부작용 없는·검증된·가장 잘하는·
   국내 최초·세계 최초·특허·독보적·노하우·효과 보장·최첨단·안전한 시술·통증 없는·흉터 없는.
4. 필드마다 **출처별 후보**를 candidates 배열로 적습니다. 같은 필드가 여러 출처에 있으면 출처마다
   후보를 하나씩 적으세요(값이 같거나 달라도 모두). 어느 후보가 맞는지 고르지 마세요.
   후보마다 출처(source), 신뢰도(confidence 0~1), 근거 원문(evidence)을 함께 적습니다.
   evidence는 그 출처 텍스트에 실제로 연속해서 존재하는 짧은 원문 발췌여야 합니다.
   source ∈ {"homepage","blog","naver","survey"} (survey = 원장 인터뷰 설문).
5. director_philosophy는 1~2문장 요약, director_career는 사실 위주 약력(과장 금지).
6. keywords는 환자가 검색할 법한 진료 키워드 5~12개(질환·시술·지역+증상).
7. competitors는 같은 진료과의 인근 경쟁 병원명만(있을 때만).

[business_hours 형식]
{"mon":"09:00-18:00","tue":...,"wed":...,"thu":...,"fri":...,"sat":"09:00-13:00","sun":"휴진"}
모르는 요일은 생략. 점심시간/휴진은 값 끝에 괄호로(예: "09:00-18:00 (12:30-14:00 점심)").

[treatments 형식]
[{"name":"치질","description":"...(200자 이내, 과장 금지)"}, ...]

[출력 — JSON 객체만, 코드펜스/설명 없이]
{
  "fields": {
    "address": {"candidates": [
      {"value": "...", "source": "naver", "confidence": 0.95, "evidence": "주소가 포함된 원문"},
      {"value": "...", "source": "homepage", "confidence": 0.8, "evidence": "홈페이지의 주소 원문"}
    ]},
    "director_name": {"candidates": [{"value": "...", "source": "homepage", "confidence": 0.9, "evidence": "입력에 실제로 있는 원문"}]},
    "phone": {"candidates": [{"value": "...", "source": "naver", "confidence": 0.95, "evidence": "전화번호가 포함된 원문"}]},
    "business_hours": {"candidates": [{"value": {"mon": "...", ...}, "source": "naver", "confidence": 0.8, "evidence": "요일별 시간이 포함된 원문"}]},
    "region": {"candidates": [{"value": ["수원시 팔달구"], "source": "naver", "confidence": 0.8, "evidence": "수원시 팔달구"}]},
    "specialties": {"candidates": [{"value": ["외과"], "source": "naver", "confidence": 0.8, "evidence": "외과"}]},
    "keywords": {"candidates": [{"value": ["치질","치루"], "source": "blog", "confidence": 0.6, "evidence": "치질과 치루가 함께 있는 원문"}]},
    "competitors": {"candidates": [{"value": ["..."], "source": "homepage", "confidence": 0.6, "evidence": "경쟁 병원명이 있는 원문"}]},
    "treatments": {"candidates": [{"value": [{"name":"...","description":"..."}], "source": "homepage", "confidence": 0.7, "evidence": "진료 항목명이 있는 원문"}]},
    "director_career": {"candidates": [{"value": "...", "source": "homepage", "confidence": 0.7, "evidence": "약력이 포함된 원문"}]},
    "director_philosophy": {"candidates": [{"value": "...", "source": "survey", "confidence": 0.8, "evidence": "진료 철학이 포함된 원문"}]},
    "website_url": {"candidates": [{"value": "https://...", "source": "naver", "confidence": 0.9, "evidence": "홈페이지 URL 원문"}]},
    "blog_url": {"candidates": [{"value": "https://...", "source": "naver", "confidence": 0.9, "evidence": "블로그 URL 원문"}]}
  }
}
근거 없는 필드는 통째로 생략하세요.
"""


@dataclass
class SourceStatus:
    name: str
    ok: bool
    reason: str | None = None


@dataclass
class AutofillResult:
    draft: dict = field(default_factory=dict)              # {field: value}
    field_meta: dict = field(default_factory=dict)         # {field: {"source","confidence"}}
    violations: list[dict] = field(default_factory=list)   # [{"field","expressions"}]
    sources: list[SourceStatus] = field(default_factory=list)
    naver_place_id: str | None = None
    rejected_fields: list[dict] = field(default_factory=list)
    # 값이 다른 출처 후보 — [{"field", "chosen": {"source","value"}, "others": [{"source","value"}]}].
    conflicts: list[dict] = field(default_factory=list)
    # 네이버 플레이스 신원(E4): STORED_ID / VERIFIED_SEARCH / UNVERIFIED / None(못 찾음).
    naver_identity: str | None = None


@dataclass
class _ProviderAttemptContext:
    logical_call_id: str
    http_attempt: int = 0

    def next_attempt(self) -> int:
        self.http_attempt += 1
        return self.http_attempt


_provider_attempt_context: ContextVar[_ProviderAttemptContext | None] = ContextVar(
    "profile_autofill_provider_attempt", default=None
)


# 출처 키 — 프롬프트의 source 값과 같다.
SOURCE_HOMEPAGE = "homepage"
SOURCE_BLOG = "blog"
SOURCE_NAVER = "naver"
SOURCE_SURVEY = "survey"
OFFICIAL_SOURCES = (SOURCE_NAVER, SOURCE_SURVEY, SOURCE_HOMEPAGE, SOURCE_BLOG)

# D34 우선순위(앞이 이긴다). 마케터 입력은 병원 행에 있으므로 적용 단계가 먼저 지킨다.
FACT_SOURCE_ORDER = (SOURCE_NAVER, SOURCE_SURVEY, SOURCE_HOMEPAGE, SOURCE_BLOG)
NARRATIVE_SOURCE_ORDER = (SOURCE_SURVEY, SOURCE_HOMEPAGE, SOURCE_BLOG, SOURCE_NAVER)
# 서술 필드 — 원장 목소리·철학은 설문이 최우선이다. 진료 항목은 이름(사실)으로 고르고 설명만
# 서술 순서로 다시 채운다(_merge_treatment_descriptions).
NARRATIVE_FIELDS = frozenset({"director_philosophy"})


async def _gather_sources(
    name: str,
    website_url: str | None,
    blog_url: str | None,
    *,
    naver_place_id: str | None = None,
    regions: Sequence[str] = (),
    survey_text: str | None = None,
) -> tuple[list[tuple[str, str]], list[SourceStatus], naver_place.NaverPlaceResult]:
    """홈/블로그/네이버를 병렬 수집. ([(출처 키, 라벨링된 텍스트 블록)], 상태, 네이버 결과)."""

    async def _fetch(label: str, url: str | None) -> tuple[str, SourceStatus]:
        if not url or not url.strip():
            return "", SourceStatus(label, ok=False, reason="URL 미입력")
        clean_url = url.strip()
        # 직접 fetch 우선(빠르고 SSRF 검증 포함) → 실패 시 Jina Reader 폴백
        # (봇 차단/peer 검증 환경 이슈 우회). 둘 다 실패하면 best-effort로 스킵.
        # fetch_url_text는 (text, error, quality) 3-튜플을 반환한다 — quality는 여기서 미사용.
        text, err, _quality = await fetch_url_text(clean_url)
        if err or not text:
            text, jina_err = await naver_place.fetch_via_jina(clean_url)
            if jina_err or not text:
                return "", SourceStatus(label, ok=False, reason=err or jina_err or "내용 없음")
        block = f"=== {label} ({clean_url}) ===\n{text[:_MAX_PER_SOURCE]}"
        return block, SourceStatus(label, ok=True)

    homepage_task = _fetch("병원 홈페이지", website_url)
    blog_task = _fetch("병원 블로그", blog_url)
    naver_task = naver_place.scrape_naver_place(
        name, place_id=naver_place_id, regions=tuple(regions)
    )
    (home_block, home_status), (blog_block, blog_status), naver_res = await asyncio.gather(
        homepage_task, blog_task, naver_task
    )

    blocks: list[tuple[str, str]] = []
    if home_block:
        blocks.append((SOURCE_HOMEPAGE, home_block))
    if blog_block:
        blocks.append((SOURCE_BLOG, blog_block))
    if naver_res.markdown:
        blocks.append(
            (SOURCE_NAVER, f"=== 네이버 플레이스 ===\n{naver_res.markdown[:_MAX_PER_SOURCE]}")
        )
    statuses = [
        home_status,
        blog_status,
        SourceStatus("네이버 플레이스", ok=bool(naver_res.markdown), reason=naver_res.reason),
    ]
    if survey_text and survey_text.strip():
        blocks.append(
            (SOURCE_SURVEY, f"=== 원장 인터뷰 설문 ===\n{survey_text.strip()[:_MAX_PER_SOURCE]}")
        )
        statuses.append(SourceStatus("원장 인터뷰 설문", ok=True))
    return blocks, statuses, naver_res


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    # 결정적 4xx(잘못된 요청·인증·권한·모델 오타)는 재시도해도 같은 실패다.
    # 재시도 1회 = 유료 호출 1회이므로 즉시 중단하고 호출부의 best-effort 폴백으로 넘긴다.
    retry=retry_if_exception(is_retryable_llm_error),
)
async def _extract_with_claude(
    name: str, aggregated_text: str, *, hospital_id: uuid.UUID | str | None = None
) -> dict:
    user_message = (
        f"[병원명]\n{name}\n\n"
        f"[출처 텍스트]\n{aggregated_text}\n\n"
        "위 출처에서만 근거를 찾아 스키마대로 JSON을 출력하세요."
    )
    # tenacity로 최대 3회 재시도되고 OpenRouter 클라이언트는 재시도를 하지 않으므로
    # 본문 1회 실행 = 유료 호출 1회다. 자동 채우기는 AE가 버튼으로 돌리는 유료 호출인데
    # 종전에는 비용 화면에 전혀 잡히지 않았다.
    from app.services import cost_guard

    await cost_guard.record_provider_call("content")
    model = _autofill_model()
    attempt_context = _provider_attempt_context.get()
    if attempt_context is None:
        attempt_context = _ProviderAttemptContext(f"profile-autofill:{uuid.uuid4()}")
    http_attempt = attempt_context.next_attempt()

    loop = asyncio.get_running_loop()
    from app.services import provider_usage

    try:
        response = await loop.run_in_executor(
            None,
            lambda: _client.chat.completions.create(
                model=model,
                max_tokens=3000,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            ),
        )
    except Exception:
        await provider_usage.record_attempt(
            provider="openrouter",
            model=model,
            workflow="PROFILE_AUTOFILL",
            cost_category="content",
            hospital_id=hospital_id,
            logical_call_id=attempt_context.logical_call_id,
            http_attempt=http_attempt,
            usage_known=False,
            idempotency_key=(
                f"{attempt_context.logical_call_id}:http-attempt:{http_attempt}"
            ),
        )
        raise

    usage = getattr(response, "usage", None)
    await provider_usage.record_attempt(
        provider="openrouter",
        model=model,
        workflow="PROFILE_AUTOFILL",
        cost_category="content",
        hospital_id=hospital_id,
        logical_call_id=attempt_context.logical_call_id,
        http_attempt=http_attempt,
        provider_request_id=str(getattr(response, "id", "") or "") or None,
        usage=usage,
        idempotency_key=f"{attempt_context.logical_call_id}:http-attempt:{http_attempt}",
    )
    raw = llm_structured_output.first_text(response)
    parsed = _parse_json_response(raw, json_module=json)
    fields = parsed.get("fields")
    return fields if isinstance(fields, dict) else {}


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _field_value_is_grounded(key: str, value: object, evidence: str) -> bool:
    """Require the proposed value to be visible in the exact cited excerpt.

    Looking anywhere in the aggregate source let a time from Tuesday accidentally
    support a fabricated Monday schedule.  The cited excerpt is the smallest trust
    boundary that keeps the field and its source coupled.
    """
    compact_source = _compact_text(evidence)
    if key in {"specialties", "keywords", "region", "competitors"} and isinstance(value, list):
        return all(_compact_text(item) in compact_source for item in value if str(item).strip())
    if key == "treatments" and isinstance(value, list):
        return all(
            _compact_text(item.get("name")) in compact_source
            for item in value
            if isinstance(item, dict) and item.get("name")
        )
    if key == "business_hours" and isinstance(value, dict):
        normalized_evidence = evidence.replace("：", ":")
        day_keys = {
            "mon": "월",
            "tue": "화",
            "wed": "수",
            "thu": "목",
            "fri": "금",
            "sat": "토",
            "sun": "일",
        }
        day_marker_pattern = re.compile(
            r"(?<![가-힣])(월요일|화요일|수요일|목요일|금요일|토요일|일요일|월|화|수|목|금|토|일)(?=\s|[:\d]|$)"
        )
        markers = list(day_marker_pattern.finditer(normalized_evidence))
        for day, hours in value.items():
            day_key = day_keys.get(str(day).lower())
            marker_index = next(
                (
                    index
                    for index, marker in enumerate(markers)
                    if day_key and marker.group(1).startswith(day_key)
                ),
                None,
            )
            if marker_index is None:
                return False
            segment_start = markers[marker_index].start()
            segment_end = (
                markers[marker_index + 1].start()
                if marker_index + 1 < len(markers)
                else len(normalized_evidence)
            )
            day_segment = normalized_evidence[segment_start:segment_end]
            time_tokens = re.findall(r"\d{1,2}:\d{2}", str(hours))
            if time_tokens and not all(token in day_segment for token in time_tokens):
                return False
            if not time_tokens and "휴진" in str(hours) and "휴진" not in day_segment:
                return False
        return bool(value)
    if key in {"director_name", "address", "phone", "website_url", "blog_url", "kakao_channel_url", "naver_place_url"}:
        return _compact_text(value) in compact_source
    return True


@dataclass
class FieldResolution:
    draft: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    rejected: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)


def _field_candidates(payload: dict) -> list[dict]:
    """필드 하나의 후보 목록. 옛 단일 모양({value, source, …})도 후보 하나로 읽는다."""

    raw = payload.get("candidates")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [payload]


def _comparable(value: object) -> str:
    return _compact_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _source_rank(field_name: str, source: str, *, naver_verified: bool) -> int:
    order = NARRATIVE_SOURCE_ORDER if field_name in NARRATIVE_FIELDS else FACT_SOURCE_ORDER
    if source == SOURCE_NAVER and not naver_verified:
        # 확인되지 않은 네이버는 순위에서 빠진다 — 다른 출처가 하나라도 있으면 진다.
        return len(order) + 1
    return order.index(source) if source in order else len(order) + 2


def _merge_treatment_descriptions(chosen: list, grounded: list[dict], *, naver_verified: bool) -> list:
    """진료 항목은 이름(사실)으로 고른 목록에, 설명(서술)만 서술 우선순위 후보에서 채운다."""

    by_rank = sorted(
        grounded,
        key=lambda item: (
            NARRATIVE_SOURCE_ORDER.index(item["source"])
            if item["source"] in NARRATIVE_SOURCE_ORDER
            and (item["source"] != SOURCE_NAVER or naver_verified)
            else len(NARRATIVE_SOURCE_ORDER)
        ),
    )
    merged: list = []
    for treatment in chosen:
        if not isinstance(treatment, dict) or not treatment.get("name"):
            merged.append(treatment)
            continue
        key = _compact_text(treatment.get("name"))
        description = None
        for candidate in by_rank:
            for other in candidate["value"] if isinstance(candidate["value"], list) else []:
                if (
                    isinstance(other, dict)
                    and _compact_text(other.get("name")) == key
                    and str(other.get("description") or "").strip()
                ):
                    description = other["description"]
                    break
            if description:
                break
        merged.append({**treatment, "description": description or treatment.get("description")})
    return merged


def _resolve_fields(
    fields: dict,
    source_texts: str | Mapping[str, str] | None = None,
    *,
    naver_verified: bool = True,
) -> FieldResolution:
    """출처별 후보를 검증하고 D34 우선순위로 하나를 고른다(결정적).

    ``source_texts``가 None이면 검증 없이 받는다(단위 시험용). 문자열이면 모든 출처가 그 본문을
    공유하고, 출처→본문 매핑이면 후보의 발췌가 **그 출처** 본문에 있어야 한다 — 네이버라고 적힌
    값이 홈페이지 문장에서 왔다면 네이버 값이 아니다.
    """

    result = FieldResolution()
    for key, payload in fields.items():
        if key not in _ALLOWED_FIELDS or not isinstance(payload, dict):
            continue
        grounded: list[dict] = []
        reasons: list[str] = []
        for candidate in _field_candidates(payload):
            if "value" not in candidate:
                continue
            value = candidate["value"]
            if value in (None, "", [], {}):
                continue
            source = str(candidate.get("source") or "inferred")
            evidence = str(candidate.get("evidence") or "").strip()
            if source_texts is not None:
                if source not in OFFICIAL_SOURCES:
                    reasons.append("공식 출처가 아닌 추론 값입니다.")
                    continue
                text = (
                    source_texts
                    if isinstance(source_texts, str)
                    else source_texts.get(source, "")
                )
                if not evidence or _compact_text(evidence) not in _compact_text(text):
                    reasons.append("출처 원문 발췌를 확인할 수 없습니다.")
                    continue
                if not _field_value_is_grounded(key, value, evidence):
                    reasons.append("제안 값이 출처 원문과 일치하지 않습니다.")
                    continue
            grounded.append(
                {
                    "value": value,
                    "source": source,
                    "confidence": _clamp_confidence(candidate.get("confidence")),
                    "evidence": evidence or None,
                }
            )
        if not grounded:
            if reasons:
                result.rejected.append({"field": key, "reason": reasons[0]})
            continue
        ranked = sorted(
            grounded,
            key=lambda item: (
                _source_rank(key, item["source"], naver_verified=naver_verified),
                -item["confidence"],
            ),
        )
        chosen = ranked[0]
        value = chosen["value"]
        if key == "treatments" and isinstance(value, list):
            value = _merge_treatment_descriptions(value, grounded, naver_verified=naver_verified)
        result.draft[key] = value
        result.meta[key] = {
            "source": chosen["source"],
            "confidence": chosen["confidence"],
            "evidence_excerpt": chosen["evidence"],
            "verified": chosen["source"] != SOURCE_NAVER or naver_verified,
        }
        chosen_key = _comparable(chosen["value"])
        others = [
            {"source": item["source"], "value": item["value"]}
            for item in ranked[1:]
            if _comparable(item["value"]) != chosen_key
        ]
        if others:
            result.conflicts.append(
                {
                    "field": key,
                    "chosen": {"source": chosen["source"], "value": chosen["value"]},
                    "others": others,
                }
            )
    return result


def _normalize_fields(
    fields: dict,
    source_text: str | Mapping[str, str] | None = None,
) -> tuple[dict, dict, list[dict]]:
    """후보 → (draft, field_meta, rejected). 충돌까지 필요하면 :func:`_resolve_fields`."""

    resolution = _resolve_fields(fields, source_text)
    return resolution.draft, resolution.meta, resolution.rejected


def _clamp_confidence(value: object) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, conf))


def _collect_violations(draft: dict) -> list[dict]:
    """draft의 텍스트/리스트 필드에 의료광고 금지 표현이 있으면 필드별로 표시."""
    violations: list[dict] = []
    for field_name in _FORBIDDEN_TEXT_FIELDS:
        value = draft.get(field_name)
        if isinstance(value, str):
            found = check_forbidden(value)
            if found:
                violations.append({"field": field_name, "expressions": found})
    for field_name in _FORBIDDEN_LIST_FIELDS:
        value = draft.get(field_name)
        if isinstance(value, list):
            found = check_forbidden(" ".join(str(v) for v in value))
            if found:
                violations.append({"field": field_name, "expressions": found})
    treatments = draft.get("treatments")
    if isinstance(treatments, list):
        joined = " ".join(
            str(t.get("description", "")) for t in treatments if isinstance(t, dict)
        )
        found = check_forbidden(joined)
        if found:
            violations.append({"field": "treatments", "expressions": found})
    return violations


async def autofill_profile(
    name: str,
    website_url: str | None = None,
    blog_url: str | None = None,
    *,
    hospital_id: uuid.UUID | str | None = None,
    naver_place_id: str | None = None,
    naver_place_url: str | None = None,
    regions: Sequence[str] = (),
    survey_text: str | None = None,
) -> AutofillResult:
    """병원명 + URL로 프로파일 초안을 생성한다(저장하지 않음).

    ``naver_place_id``/``naver_place_url``은 병원 행에 저장된 값이다 — 있으면 네이버를 검색하지 않고
    그 플레이스를 읽는다(E4). ``regions``는 검색 결과의 지역 확인에, ``survey_text``(가림 처리된 원장
    인터뷰 설문 본문)는 서술 필드의 최우선 출처로 쓴다. 소스를 하나도 못 모으면 draft는 비고
    sources에 사유가 담긴다.
    """
    blocks, statuses, naver_res = await _gather_sources(
        name,
        website_url,
        blog_url,
        naver_place_id=naver_place.stored_place_id(naver_place_id, naver_place_url),
        regions=regions,
        survey_text=survey_text,
    )
    result = AutofillResult(
        sources=statuses,
        naver_place_id=naver_res.place_id,
        naver_identity=naver_res.identity,
    )

    if not blocks:
        return result  # 모든 소스 실패 — 빈 초안 + 사유만 반환

    aggregated = "\n\n".join(block for _source, block in blocks)
    source_texts = {source: block for source, block in blocks}
    attempt_context = _ProviderAttemptContext(
        f"profile-autofill:{hospital_id or 'unassigned'}:{uuid.uuid4()}"
    )
    attempt_token = _provider_attempt_context.set(attempt_context)
    try:
        fields = await _extract_with_claude(name, aggregated, hospital_id=hospital_id)
    except Exception as exc:  # noqa: BLE001 — 추출 실패는 치명적이지 않음
        logger.warning("autofill extraction failed for %s: %s", name, exc)
        return result
    finally:
        _provider_attempt_context.reset(attempt_token)

    resolution = _resolve_fields(fields, source_texts, naver_verified=naver_res.verified)
    draft, meta = resolution.draft, resolution.meta

    # 네이버 place_id는 스크랩에서 직접 얻은 값이 LLM 추출보다 신뢰도 높다. 신원이 확인되지
    # 않았으면 초안에만 남는다(verified=False → 자동 적용 안 함).
    if naver_res.place_id:
        draft["naver_place_id"] = naver_res.place_id
        meta["naver_place_id"] = {
            "source": SOURCE_NAVER,
            "confidence": 0.95,
            "verified": naver_res.verified,
        }
    if naver_res.canonical_url:
        draft["naver_place_url"] = naver_res.canonical_url
        meta["naver_place_url"] = {
            "source": SOURCE_NAVER,
            "confidence": 1.0,
            "verified": naver_res.verified,
        }

    result.draft = draft
    result.field_meta = meta
    result.violations = _collect_violations(draft)
    result.rejected_fields = resolution.rejected
    result.conflicts = resolution.conflicts
    return result
