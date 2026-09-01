"""측정 질의 문장 → AIQueryTarget 구조 필드 복원.

## 왜 필요한가

V0 이후 자동 시드된 `AIQueryTarget`은 지금까지 `target_intent="증상 탐색"`,
`region_terms=[]`, `treatment=None`, `condition_or_symptom=None`으로 만들어졌다
(`api/admin/query_targets.py`). 그래서 콘텐츠 슬롯이 어떤 타깃을 답할지 고를 때 쓰는
유형 친화도(`content_target_planner._content_type_affinity`)가 **모든 타깃에서 같은
값**이 되어 변별력이 0이었다. 측정은 "어느 질문에서 안 나오는지"를 알고 있는데
생성은 그걸 못 쓰는 상태였다.

## 어떻게 복원하는가

질의는 전부 `sov_engine._TEMPLATE_SPECS`의 템플릿으로 만들어진다. 즉 문장 안에
지역·진료과·임상 키워드가 어느 자리에 들어갔는지 템플릿이 이미 알고 있다. 따라서
질의 원문을 템플릿의 고정부로 되짚어 치환값을 되찾으면 병원 프로파일을 다시 읽지
않고도 구조 필드를 만들 수 있다(= 시드 함수의 DB 쿼리 순서를 건드리지 않는다).

되찾은 키워드는 질의 생성과 **같은 분류기**(`keyword_analysis.analyze_keyword`)로
분류한다. 두 경로가 다른 기준을 쓰면 "측정에 쓴 말"과 "글에 쓸 말"이 어긋난다.

모든 판단은 결정적이다 — 같은 문장은 항상 같은 구조를 돌려준다(테스트 가능).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.keyword_analysis import (
    KeywordAnalysis,
    KeywordClass,
    _looks_like_region,
    analyze_keyword,
    normalize,
)
from app.services.sov_engine import _PROCEDURAL, _TEMPLATE_SPECS

# ── 환자 의도 (target_intent) ────────────────────────────────────────────────
# 기존 시드 기본값 "증상 탐색"을 그대로 유지해, 이미 저장된 값과 새 값이 같은 어휘를
# 쓰게 한다. AE가 Admin에서 보던 문자열이 바뀌지 않는 것도 중요하다.
INTENT_SYMPTOM = "증상 탐색"          # "{질환} 진료를 받으려는데 {지역} 어느 병원…"
INTENT_RECOMMENDATION = "추천 탐색"    # "{지역} {진료과} 추천해줘"
INTENT_COMPARISON = "비교 검토"        # "어디가 좋은지 비교해줘"
INTENT_AVAILABILITY = "진료 가능 확인"  # "{지역} {키워드} 진료 가능한 병원"
INTENT_COST = "비용 확인"              # "진료비 어느 정도야?"
INTENT_INFO = "정보 탐색"              # 지역 없는 의학 설명형

# COLUMN·HEALTH처럼 "설명하는 글"이 맞붙는 의도. 추천/진료가능처럼 즉시 내원을 묻는
# 질문과 구분한다.
INFO_LIKE_INTENTS: frozenset[str] = frozenset(
    {INTENT_INFO, INTENT_COST, INTENT_COMPARISON}
)

# 템플릿 → 환자 의도. 템플릿 문자열을 키로 써서 sov_engine에서 템플릿이 바뀌면
# 매핑 누락이 곧바로 드러나게 한다(누락은 기본값 INTENT_SYMPTOM으로 폴백).
_TEMPLATE_INTENTS: dict[str, str] = {
    "{region} {clinic} 추천해줘": INTENT_RECOMMENDATION,
    "{region} {specialty} 전문의 추천": INTENT_RECOMMENDATION,
    "{region} {clinic} 어디가 좋은지 비교해줘": INTENT_COMPARISON,
    "{sub_region}에서 {specialty} 진료 받을 수 있는 병원 알려줘": INTENT_AVAILABILITY,
    "{region} {specialty} 진료비 어느 정도야?": INTENT_COST,
    "{keyword} 진료를 받으려는데 {region} 어느 병원으로 가야 해?": INTENT_SYMPTOM,
    "{region}에서 {keyword} 치료하는 병원 알려줘": INTENT_SYMPTOM,
    "{sub_region} {keyword} 진료 가능한 병원": INTENT_AVAILABILITY,
    "{region}에서 {keyword} 받을 수 있는 병원 알려줘": INTENT_AVAILABILITY,
    "{sub_region} {keyword} 가능한 병원 추천해줘": INTENT_RECOMMENDATION,
    "{keyword} 초기 증상이 뭔지 알려줘": INTENT_INFO,
    "{keyword} 치료하려면 어떤 전문의한테 가야 해?": INTENT_INFO,
    "{keyword} 치료 비용이 얼마나 드는지 알려줘": INTENT_COST,
    "{keyword} 비용이 얼마나 드는지 알려줘": INTENT_COST,
    "{keyword} 후 회복 기간 얼마나 돼?": INTENT_INFO,
}

# 템플릿을 못 찾았을 때(레거시 문장·AE 수기 입력) 문장 표지로 의도를 되짚는다.
# 긴 표지부터 본다 — "비교해줘"가 "추천해줘"보다 앞서야 "비교해줘"가 추천으로 접히지 않는다.
_INTENT_MARKERS: tuple[tuple[str, str], ...] = (
    ("비교해줘", INTENT_COMPARISON),
    ("어디가 좋은지", INTENT_COMPARISON),
    ("진료비", INTENT_COST),
    ("비용", INTENT_COST),
    ("얼마", INTENT_COST),
    ("진료 가능", INTENT_AVAILABILITY),
    ("받을 수 있는", INTENT_AVAILABILITY),
    ("가능한 병원", INTENT_AVAILABILITY),
    ("추천", INTENT_RECOMMENDATION),
    ("초기 증상", INTENT_INFO),
    ("회복 기간", INTENT_INFO),
)

# 환자가 "묻는 형태"인가 — FAQ 슬롯이 붙을 수 있는지 판단한다. 측정 질의는 대부분
# 명령형("알려줘")이라 물음표만 보면 대부분 놓친다.
_QUESTION_MARKERS: tuple[str, ...] = (
    "?",
    "알려줘",
    "추천해줘",
    "추천",
    "비교해줘",
    "가야 해",
    "어디가",
    "얼마",
    "뭔지",
    "어느 정도",
    "가능한 병원",
)

_PLACEHOLDER_RE = re.compile(r"\{(region|sub_region|keyword|specialty|clinic)\}")

# 기관어 자체는 진료과가 아니다. 폴백 파싱이 "병원"을 specialty로 저장하면 프롬프트에
# "전문 분야: 병원"이 들어간다.
_GENERIC_INSTITUTION_WORDS: frozenset[str] = frozenset(
    {"병원", "의원", "클리닉", "한의원"}
)

# 템플릿 파싱 후보 폭발 방지. 치환자는 최대 2개고 문장은 60자 내외라 실제로는
# 수백 개를 넘지 않지만, 손으로 넣은 이상한 문장이 들어와도 상한이 있어야 한다.
_MAX_BINDINGS = 400


@dataclass(frozen=True, slots=True)
class QueryStructure:
    """질의 한 문장에서 되찾은 구조.

    `clinical_keyword`가 이 구조의 핵심이다 — 글의 주제 키워드이자, 생성 결과가
    실제로 이 질문에 답했는지 검증할 때 찾는 말이다.
    """

    target_intent: str = INTENT_SYMPTOM
    region_terms: list[str] = field(default_factory=list)
    specialty: str | None = None
    condition_or_symptom: str | None = None
    treatment: str | None = None
    is_question: bool = True
    matched_template: str | None = None

    @property
    def clinical_keyword(self) -> str | None:
        """글의 1순위 키워드. 질환·증상이 시술보다 앞선다(환자가 먼저 쓰는 말)."""
        return self.condition_or_symptom or self.treatment or self.specialty


def _template_parts(template: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    pos = 0
    for match in _PLACEHOLDER_RE.finditer(template):
        if match.start() > pos:
            parts.append(("lit", template[pos:match.start()]))
        parts.append(("ph", match.group(1)))
        pos = match.end()
    if pos < len(template):
        parts.append(("lit", template[pos:]))
    return parts


def _collect_bindings(
    text: str,
    parts: list[tuple[str, str]],
    index: int,
    pos: int,
    bound: dict[str, str],
    out: list[dict[str, str]],
) -> None:
    """템플릿 고정부를 순서대로 소비하며 가능한 치환 조합을 모두 모은다.

    `{sub_region} {keyword} 진료 가능한 병원`처럼 치환자 두 개가 공백 하나로 붙어
    있으면 분해가 하나로 정해지지 않는다. 여기서는 후보를 전부 만들고, 어느 분해가
    맞는지는 `_binding_rank`가 키워드 분류로 결정한다.
    """
    if len(out) >= _MAX_BINDINGS:
        return
    if index == len(parts):
        if pos == len(text):
            out.append(dict(bound))
        return

    kind, value = parts[index]
    if kind == "lit":
        if text.startswith(value, pos):
            _collect_bindings(text, parts, index + 1, pos + len(value), bound, out)
        return

    for end in range(pos + 1, len(text) + 1):
        segment = text[pos:end]
        # 치환값 앞뒤에 공백이 남으면 템플릿 고정부를 잘못 먹은 것이다.
        if segment != segment.strip():
            continue
        bound[value] = segment
        _collect_bindings(text, parts, index + 1, end, bound, out)
        if len(out) >= _MAX_BINDINGS:
            break
    bound.pop(value, None)


def _binding_rank(
    binding: dict[str, str], applies: frozenset
) -> tuple[int, int, int, int]:
    """분해 후보의 우열. 작을수록 좋다.

    1순위는 "되찾은 키워드가 템플릿이 허용한 종류인가"다 — 질의 생성기가 그 조건으로만
    문장을 만들었으므로, 이 조건을 만족하는 분해가 원본 분해다.
    """
    keyword = binding.get("keyword")
    class_miss = 0
    if keyword:
        analysis = analyze_keyword(keyword)
        if applies and analysis.keyword_class not in applies:
            class_miss = 1
        elif analysis.keyword_class is KeywordClass.UNKNOWN:
            # 분류 실패는 오답은 아니지만, 사전에 걸린 분해가 있으면 그쪽을 택한다.
            class_miss = 1 if applies else 0
    # 지역은 보통 한 토큰("강남역")이다. 토큰이 적은 분해를 선호한다.
    region_tokens = len(binding.get("region", "").split())
    sub_region_tokens = len(binding.get("sub_region", "").split())
    return (class_miss, region_tokens, sub_region_tokens, len(keyword or ""))


def _match_template(text: str) -> tuple[str, frozenset, dict[str, str]] | None:
    best: tuple[tuple, str, frozenset, dict[str, str]] | None = None
    for order, (template, _intent, applies) in enumerate(_TEMPLATE_SPECS):
        parts = _template_parts(template)
        bindings: list[dict[str, str]] = []
        _collect_bindings(text, parts, 0, 0, {}, bindings)
        if not bindings:
            continue
        binding = min(bindings, key=lambda item: (_binding_rank(item, applies), sorted(item.items())))
        rank = (*_binding_rank(binding, applies), order)
        if best is None or rank < best[0]:
            best = (rank, template, applies, binding)
    if best is None:
        return None
    return best[1], best[2], best[3]


def _assign_clinical(
    analysis: KeywordAnalysis, applies: frozenset
) -> tuple[str | None, str | None]:
    """(condition_or_symptom, treatment).

    질환·증상·부위는 "진료받는 대상"이고 시술·진료방식은 "받는 대상"이다.
    분류 실패(UNKNOWN)는 템플릿이 허용한 종류로 되짚는다 — 시술 전용 템플릿에서
    나온 말은 시술이다.
    """
    klass = analysis.keyword_class
    term = analysis.canonical_term or analysis.raw
    if klass in (KeywordClass.PROCEDURE, KeywordClass.CARE_SERVICE):
        return None, term
    if klass in (KeywordClass.DISEASE, KeywordClass.SYMPTOM, KeywordClass.BODY_PART):
        return term, None
    if applies and applies == _PROCEDURAL:
        return None, term
    return term, None


def _fallback_structure(text: str) -> QueryStructure:
    """템플릿을 못 찾은 문장(레거시·수기 입력)에서 최대한 되짚는다."""
    tokens = normalize(text).split()
    region_terms = [token for token in tokens if _looks_like_region(token)]
    intent = INTENT_SYMPTOM
    for marker, value in _INTENT_MARKERS:
        if marker in text:
            intent = value
            break

    analysis = analyze_keyword(text, region_terms or None)
    condition, treatment = (None, None)
    # 분류가 사전·규칙에 걸린 경우에만 임상 키워드로 인정한다. UNKNOWN 폴백을 그대로
    # 채우면 문장 전체("아무 문장이나 좋은 곳")가 질환명으로 저장돼, 이후 프롬프트와
    # 검증이 그 말을 제목에서 찾게 된다.
    if analysis.keyword_class not in (
        KeywordClass.SEARCH_PHRASE,
        KeywordClass.UNKNOWN,
    ):
        condition, treatment = _assign_clinical(analysis, frozenset())
    specialty = analysis.embedded_specialty
    if specialty and specialty.strip() in _GENERIC_INSTITUTION_WORDS:
        specialty = None
    if analysis.embedded_region and analysis.embedded_region not in region_terms:
        region_terms = [analysis.embedded_region, *region_terms]

    return QueryStructure(
        target_intent=intent,
        region_terms=_dedupe(region_terms),
        specialty=specialty,
        condition_or_symptom=condition,
        treatment=treatment,
        is_question=_is_question(text),
        matched_template=None,
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _is_question(text: str) -> bool:
    stripped = (text or "").strip()
    return any(marker in stripped for marker in _QUESTION_MARKERS)


def describe_query_text(query_text: str) -> QueryStructure:
    """질의 원문 한 문장에서 구조 필드를 되찾는다. 예외를 던지지 않는다."""
    text = (query_text or "").strip()
    if not text:
        return QueryStructure(is_question=False)

    matched = _match_template(text)
    if matched is None:
        return _fallback_structure(text)

    template, applies, binding = matched
    intent = _TEMPLATE_INTENTS.get(template, INTENT_SYMPTOM)

    region_terms = _dedupe([binding.get("region", ""), binding.get("sub_region", "")])

    specialty = (binding.get("specialty") or "").strip() or None
    if not specialty and binding.get("clinic"):
        # clinic_phrase는 진료과 뒤에 ' 병원'을 붙인 표현이다. 진료과만 되돌린다.
        specialty = re.sub(r"\s*병원$", "", binding["clinic"]).strip() or None

    condition, treatment = (None, None)
    keyword = (binding.get("keyword") or "").strip()
    if keyword:
        condition, treatment = _assign_clinical(analyze_keyword(keyword), applies)

    return QueryStructure(
        target_intent=intent,
        region_terms=region_terms,
        specialty=specialty,
        condition_or_symptom=condition,
        treatment=treatment,
        is_question=_is_question(text),
        matched_template=template,
    )


# 시드가 지금까지 넣어온 기본값. "사람이 고른 값"이 아니라 "채우지 못했다"는 표시이므로
# 백필에서 덮어쓸 수 있다. AE가 Admin에서 직접 바꾼 값은 이 목록에 없어 보존된다.
_PLACEHOLDER_INTENTS: frozenset[str] = frozenset({"", INTENT_SYMPTOM})


def apply_structure_to_target(target: object, *, force: bool = False) -> bool:
    """비어 있는 구조 필드만 채운다. 변경이 있었으면 True.

    AE가 Admin에서 손으로 넣은 값은 절대 덮어쓰지 않는다(force=False 기본).
    `target_intent`만 예외로, 시드가 넣은 자리표시자 값이면 되찾은 의도로 바꾼다.
    """
    name = getattr(target, "name", None)
    if not name:
        return False
    structure = describe_query_text(str(name))
    changed = False

    if structure.region_terms and (force or not getattr(target, "region_terms", None)):
        target.region_terms = list(structure.region_terms)
        changed = True
    if structure.specialty and (force or not getattr(target, "specialty", None)):
        target.specialty = structure.specialty
        changed = True
    if structure.condition_or_symptom and (
        force or not getattr(target, "condition_or_symptom", None)
    ):
        target.condition_or_symptom = structure.condition_or_symptom
        changed = True
    if structure.treatment and (force or not getattr(target, "treatment", None)):
        target.treatment = structure.treatment
        changed = True

    current_intent = str(getattr(target, "target_intent", "") or "").strip()
    if structure.target_intent and (
        force or current_intent in _PLACEHOLDER_INTENTS
    ) and current_intent != structure.target_intent:
        target.target_intent = structure.target_intent
        changed = True

    return changed


def target_is_question_form(target: object) -> bool:
    """FAQ 친화도 판정용 — 저장된 구조 필드가 없어도 이름으로 되짚는다."""
    name = getattr(target, "name", None)
    return _is_question(str(name)) if name else False


# ── 측정 질의 → 글에 쓰는 표현 ───────────────────────────────────────────────
# 측정 질의는 환자가 AI에게 던지는 반말 명령형("추천해줘")이다. 공개되는 글의 FAQ
# 질문 문장으로 그대로 쓰면 어색하므로 존댓말 의문문으로 바꾼다. 뜻은 바꾸지 않는다 —
# 이 문장이 곧 "측정에서 우리가 답해야 하는 질문"이기 때문이다.
# 긴 표지부터 본다(가장 구체적인 어미가 먼저 걸려야 한다).
_QUESTION_REWRITES: tuple[tuple[str, str], ...] = (
    ("어느 병원으로 가야 해?", "어느 병원으로 가야 하나요?"),
    ("어떤 전문의한테 가야 해?", "어떤 전문의에게 가야 하나요?"),
    ("얼마나 드는지 알려줘", "얼마나 드나요?"),
    ("어디가 좋은지 비교해줘", "어디가 좋은가요?"),
    ("뭔지 알려줘", "무엇인가요?"),
    ("얼마나 돼?", "얼마나 되나요?"),
    ("어느 정도야?", "어느 정도인가요?"),
    ("가능한 병원 추천해줘", "가능한 병원은 어디인가요?"),
    ("추천해줘", "추천해 주시겠어요?"),
    ("알려줘", "알려주시겠어요?"),
    ("비교해줘", "비교해 주시겠어요?"),
)


def natural_patient_question(query_text: str) -> str:
    """측정 질의를 환자가 실제로 쓸 법한 존댓말 한 문장 질문으로 정규화한다.

    결정적이며 뜻을 바꾸지 않는다. 규칙에 걸리지 않으면 물음표만 보정한다.
    """
    text = " ".join((query_text or "").split())
    if not text:
        return ""
    for suffix, replacement in _QUESTION_REWRITES:
        if text.endswith(suffix):
            return f"{text[: -len(suffix)]}{replacement}".strip()
    if text.endswith("?"):
        return text
    if text.endswith(("병원", "추천")):
        return f"{text}은 어디인가요?"
    return f"{text}?"


_PARTICLE_SUFFIX_RE = re.compile(r"(에서|에게|에|의|은|는|이|가|을|를|로|으로)$")

# 조사·기관어만 남은 토큰은 글의 주제어가 될 수 없다.
_STOP_TOKENS: frozenset[str] = frozenset(
    {
        "병원", "의원", "클리닉", "한의원", "추천해줘", "추천", "알려줘", "비교해줘",
        "어디가", "좋은지", "어느", "가야", "해?", "진료", "진료비", "전문의",
        "받을", "수", "있는", "가능한", "치료하는", "받으려는데", "얼마나", "드는지",
        "비용이", "초기", "증상이", "뭔지", "정도야?", "회복", "기간", "돼?",
        "치료하려면", "어떤", "전문의한테", "치료", "비용", "진료를", "병원으로",
    }
)


def clinical_keyword_from_query(
    query_text: str, region_terms: list[str] | None = None
) -> str | None:
    """질의에서 글의 주제어가 될 만한 가장 긴 비지역 토큰.

    구조 필드(condition/treatment)가 비어 있는 레거시·수기 타깃의 마지막 안전망이다.
    """
    regions = {str(term).strip() for term in (region_terms or []) if str(term).strip()}
    candidates = []
    for raw in " ".join((query_text or "").split()).split():
        # 조사를 떼고 본다 — "강남역에서"는 지역이지 주제어가 아니다.
        token = _PARTICLE_SUFFIX_RE.sub("", raw) or raw
        if (
            token in _STOP_TOKENS
            or token in regions
            or _looks_like_region(token)
            or len(token) < 2
        ):
            continue
        candidates.append(token)
    if not candidates:
        return None
    # 가장 긴 토큰 → 동률이면 먼저 나온 것(결정적).
    best = candidates[0]
    for token in candidates[1:]:
        if len(token) > len(best):
            best = token
    return best
