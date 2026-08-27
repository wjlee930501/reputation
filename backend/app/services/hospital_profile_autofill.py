"""병원 프로파일 자동 채우기 — 홈페이지/블로그/네이버 플레이스 스크랩 + Claude 구조화 추출.

원칙(불변식):
- **자동 커밋하지 않는다.** 초안(draft) + 필드별 출처/신뢰도 + 의료광고 위반 표시를 반환하고,
  AE가 검수 후 기존 PATCH /profile 로 저장한다.
- 모든 소스 수집은 **best-effort**. 일부 소스가 실패해도 가능한 필드만 채운다.
- 추출 텍스트는 medical_filter로 검사해 위반을 **필드별로 표시**한다(자동 삭제하지 않음 —
  director_career 같은 문장을 임의로 잘라내면 의미가 깨지므로 AE가 판단).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services import naver_place
from app.services.asset_extractor import fetch_url_text
from app.services.content_engine import _parse_json_response
from app.utils.medical_filter import check_forbidden

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(
    api_key=settings.ANTHROPIC_API_KEY,
    timeout=90.0,
    max_retries=0,  # tenacity가 백오프로 재시도
)

# 소스별 입력 텍스트 상한 — 토큰/비용 통제. 합쳐 ~50K자.
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
4. 각 필드에 출처(source), 신뢰도(confidence 0~1), 근거 원문(evidence)을 함께 적습니다.
   evidence는 입력 출처에 실제로 연속해서 존재하는 짧은 원문 발췌여야 합니다.
   source ∈ {"homepage","blog","naver","inferred"}. 여러 출처면 가장 신뢰 높은 것.
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
    "director_name": {"value": "...", "source": "homepage", "confidence": 0.9, "evidence": "입력에 실제로 있는 원문"},
    "address": {"value": "...", "source": "naver", "confidence": 0.95, "evidence": "주소가 포함된 원문"},
    "phone": {"value": "...", "source": "naver", "confidence": 0.95, "evidence": "전화번호가 포함된 원문"},
    "business_hours": {"value": {"mon": "...", ...}, "source": "naver", "confidence": 0.8, "evidence": "요일별 시간이 포함된 원문"},
    "region": {"value": ["수원시 팔달구"], "source": "naver", "confidence": 0.8, "evidence": "수원시 팔달구"},
    "specialties": {"value": ["외과"], "source": "naver", "confidence": 0.8, "evidence": "외과"},
    "keywords": {"value": ["치질","치루"], "source": "blog", "confidence": 0.6, "evidence": "치질과 치루가 함께 있는 원문"},
    "competitors": {"value": ["..."], "source": "homepage", "confidence": 0.6, "evidence": "경쟁 병원명이 있는 원문"},
    "treatments": {"value": [{"name":"...","description":"..."}], "source": "homepage", "confidence": 0.7, "evidence": "진료 항목명이 있는 원문"},
    "director_career": {"value": "...", "source": "homepage", "confidence": 0.7, "evidence": "약력이 포함된 원문"},
    "director_philosophy": {"value": "...", "source": "homepage", "confidence": 0.6, "evidence": "진료 철학이 포함된 원문"},
    "website_url": {"value": "https://...", "source": "naver", "confidence": 0.9, "evidence": "홈페이지 URL 원문"},
    "blog_url": {"value": "https://...", "source": "naver", "confidence": 0.9, "evidence": "블로그 URL 원문"}
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


async def _gather_sources(
    name: str, website_url: str | None, blog_url: str | None
) -> tuple[list[str], list[SourceStatus], naver_place.NaverPlaceResult]:
    """홈/블로그/네이버를 병렬 수집. (출처별 라벨링된 텍스트 블록, 상태, 네이버 결과)."""

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
    naver_task = naver_place.scrape_naver_place(name)
    (home_block, home_status), (blog_block, blog_status), naver_res = await asyncio.gather(
        homepage_task, blog_task, naver_task
    )

    blocks: list[str] = []
    if home_block:
        blocks.append(home_block)
    if blog_block:
        blocks.append(blog_block)
    if naver_res.markdown:
        blocks.append(f"=== 네이버 플레이스 ===\n{naver_res.markdown[:_MAX_PER_SOURCE]}")

    statuses = [
        home_status,
        blog_status,
        SourceStatus("네이버 플레이스", ok=bool(naver_res.markdown), reason=naver_res.reason),
    ]
    return blocks, statuses, naver_res


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _extract_with_claude(
    name: str, aggregated_text: str, *, hospital_id: uuid.UUID | str | None = None
) -> dict:
    user_message = (
        f"[병원명]\n{name}\n\n"
        f"[출처 텍스트]\n{aggregated_text}\n\n"
        "위 출처에서만 근거를 찾아 스키마대로 JSON을 출력하세요."
    )
    # tenacity로 최대 3회 재시도되고 Anthropic 클라이언트는 재시도를 하지 않으므로
    # 본문 1회 실행 = 유료 호출 1회다. 자동 채우기는 AE가 버튼으로 돌리는 유료 호출인데
    # 종전에는 비용 화면에 전혀 잡히지 않았다.
    from app.services import cost_guard

    await cost_guard.record_provider_call("content")

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: _client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=3000,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        ),
    )
    from app.services.hospital_usage import record_usage

    usage = getattr(response, "usage", None)
    await record_usage(
        hospital_id=hospital_id,
        kind="onboarding",
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
    )
    raw = response.content[0].text
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


def _normalize_fields(
    fields: dict,
    source_text: str | None = None,
) -> tuple[dict, dict, list[dict]]:
    """Claude의 {field:{value,source,confidence}} → (draft, field_meta)."""
    draft: dict = {}
    meta: dict = {}
    rejected: list[dict] = []
    for key, payload in fields.items():
        if key not in _ALLOWED_FIELDS or not isinstance(payload, dict):
            continue
        if "value" not in payload:
            continue
        value = payload["value"]
        if value in (None, "", [], {}):
            continue
        source = str(payload.get("source") or "inferred")
        evidence = str(payload.get("evidence") or "").strip()
        if source_text is not None:
            if source not in {"homepage", "blog", "naver"}:
                rejected.append({"field": key, "reason": "공식 출처가 아닌 추론 값입니다."})
                continue
            if not evidence or _compact_text(evidence) not in _compact_text(source_text):
                rejected.append({"field": key, "reason": "출처 원문 발췌를 확인할 수 없습니다."})
                continue
            if not _field_value_is_grounded(key, value, evidence):
                rejected.append({"field": key, "reason": "제안 값이 출처 원문과 일치하지 않습니다."})
                continue
        draft[key] = value
        meta[key] = {
            "source": source,
            "confidence": _clamp_confidence(payload.get("confidence")),
            "evidence_excerpt": evidence or None,
        }
    return draft, meta, rejected


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
) -> AutofillResult:
    """병원명 + URL로 프로파일 초안을 생성한다(저장하지 않음).

    소스를 하나도 못 모으면 draft는 비고 sources에 사유가 담긴다.
    """
    blocks, statuses, naver_res = await _gather_sources(name, website_url, blog_url)
    result = AutofillResult(sources=statuses, naver_place_id=naver_res.place_id)

    if not blocks:
        return result  # 모든 소스 실패 — 빈 초안 + 사유만 반환

    aggregated = "\n\n".join(blocks)
    try:
        fields = await _extract_with_claude(name, aggregated, hospital_id=hospital_id)
    except Exception as exc:  # noqa: BLE001 — 추출 실패는 치명적이지 않음
        logger.warning("autofill extraction failed for %s: %s", name, exc)
        return result

    draft, meta, rejected = _normalize_fields(fields, aggregated)

    # 네이버 place_id는 스크랩에서 직접 얻은 값이 LLM 추출보다 신뢰도 높다.
    if naver_res.place_id:
        draft["naver_place_id"] = naver_res.place_id
        meta["naver_place_id"] = {"source": "naver", "confidence": 0.95}
    if naver_res.canonical_url:
        draft["naver_place_url"] = naver_res.canonical_url
        meta["naver_place_url"] = {"source": "naver", "confidence": 1.0}

    result.draft = draft
    result.field_meta = meta
    result.violations = _collect_violations(draft)
    result.rejected_fields = rejected
    return result
