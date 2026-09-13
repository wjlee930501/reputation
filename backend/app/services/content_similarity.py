"""한국어 주제 유사도 — 결정적이고 값싼 문자 bigram 비교만 쓴다.

두 곳에서 쓴다.

1. 참고자료 주제 적합성 (`content_engine`): 모델이 붙인 references 중 글의 주제와
   **명백히** 어긋나는 항목만 떨궈낸다. 판정이 애매하면 유지한다 — 근거를 잘못
   버리면 발행이 막히지만, 남겨 두면 사람이 나중에 볼 수 있다.
2. 중복 주제 가드 (`workers.tasks`): 최근 발행 제목과 사실상 같은 글을 만들었을 때
   한 번의 재작성으로 각도를 바꾸게 하고, 그래도 비슷하면 **글은 통과시키고**
   기록만 남긴다. 어떤 경우에도 거절 코드·인시던트·Slack이 되지 않는다.

형태소 분석기를 쓰지 않는 이유: 운영 의존성을 늘리지 않고도 '허리디스크 초기 증상'과
'허리디스크 초기증상 자가진단'처럼 표기만 다른 중복은 문자 bigram으로 충분히 잡힌다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

# 제목 bigram Jaccard 기준. 보수적으로 높게 둔다 — 같은 키워드의 다른 각도(예:
# '허리디스크 초기 증상' vs '허리디스크 수술 없이 치료')를 중복으로 부르지 않는다.
DUPLICATE_TITLE_THRESHOLD = 0.6

# 참고자료 제목의 한 토큰이 글 주제 bigram 풀과 이만큼도 겹치지 않으면 그 토큰은
# 이 글의 주제가 아니다. 토큰 하나라도 이 기준을 넘으면 그 참고자료는 유지한다.
REFERENCE_TOKEN_MATCH_MIN = 0.5

_NON_TOPIC_CHARS = re.compile(r"[^0-9a-z가-힣]+")
_HANGUL = re.compile(r"[가-힣]")

# 참고자료 제목에 흔한, 주제를 가리지 못하는 일반 단어. 이 토큰들은 "이 자료가 다른
# 주제다"라는 판단 근거가 되지 못하므로 점수 계산에서 제외한다.
_GENERIC_REFERENCE_TOKENS: frozenset[str] = frozenset(
    {
        "건강",
        "건강정보",
        "검사",
        "관리",
        "국가",
        "국민",
        "가이드",
        "가이드라인",
        "권고",
        "권고안",
        "안내",
        "의료",
        "의학",
        "일반",
        "자료",
        "정보",
        "지침",
        "진료",
        "진료지침",
        "질병",
        "질환",
        "치료",
        "통계",
        "포털",
        "표준",
        "학회",
        "한국",
        "환자",
        "협회",
        "홈페이지",
        "예방",
        "보건",
    }
)

# 글 주제 쪽에만 적용하는 아주 작은 관련어 묶음. 한 항목이 글의 주제어에 있으면 같은
# 묶음의 나머지도 주제 풀에 넣는다. **넓히기만** 하므로 잘못된 제거를 만들지 않는다
# (예: '허리디스크' 글에 붙은 '요통' 자료를 근거 없이 떨구지 않게 한다).
_RELATED_TOPIC_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"허리", "요추", "요통", "척추", "디스크", "추간판", "협착증"}),
    frozenset({"무릎", "슬관절", "반월상연골", "관절염"}),
    frozenset({"어깨", "견관절", "회전근개", "오십견"}),
    frozenset({"목", "경추", "거북목", "일자목"}),
    frozenset({"발", "족부", "족저근막염", "무지외반증"}),
    frozenset({"위", "위염", "소화", "속쓰림", "역류성식도염"}),
    frozenset({"대장", "대장암", "대장내시경", "치질", "치핵"}),
    frozenset({"피부", "아토피", "여드름", "건선"}),
)


def normalize_topic_text(value: object) -> str:
    """비교용 정규화 — 소문자화 후 한글·영숫자만 남기고 공백·구두점을 제거한다."""
    return _NON_TOPIC_CHARS.sub("", str(value or "").lower())


def char_bigrams(value: object) -> set[str]:
    """정규화한 문자열의 인접 2글자 집합. 1글자면 그 글자 자체를 쓴다."""
    text = normalize_topic_text(value)
    if not text:
        return set()
    if len(text) == 1:
        return {text}
    return {text[index : index + 2] for index in range(len(text) - 1)}


def topic_similarity(a: object, b: object) -> float:
    """두 문자열의 문자 bigram Jaccard 유사도 (0.0~1.0)."""
    left = char_bigrams(a)
    right = char_bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _expand_related_terms(terms: Iterable[object]) -> list[str]:
    normalized = [normalize_topic_text(term) for term in terms]
    expanded = [term for term in normalized if term]
    for group in _RELATED_TOPIC_GROUPS:
        if any(member in term for term in expanded for member in group):
            expanded.extend(group)
    return expanded


def topic_bigram_pool(terms: Iterable[object]) -> set[str]:
    """글의 주제어들(키워드·제목·첫 H2·진료 서사)을 하나의 bigram 풀로 만든다."""
    pool: set[str] = set()
    for term in _expand_related_terms(terms):
        pool |= char_bigrams(term)
    return pool


def _topical_tokens(text: object, ignored_tokens: frozenset[str]) -> list[str]:
    """참고자료 제목에서 주제를 판단할 수 있는 한글 토큰만 남긴다.

    기관명·일반 단어(진료지침, 건강정보 등)와 영문 토큰은 제외한다. 영문 제목만 있는
    국제 자료는 이 방식으로 주제를 판단할 수 없으므로 아예 채점하지 않는다(= 유지).
    """
    tokens: list[str] = []
    for raw in re.split(r"[^0-9A-Za-z가-힣]+", str(text or "")):
        token = normalize_topic_text(raw)
        if len(token) < 2 or not _HANGUL.search(token):
            continue
        if token in ignored_tokens or token in _GENERIC_REFERENCE_TOKENS:
            continue
        tokens.append(token)
    return tokens


def reference_topic_match(
    reference_text: object,
    article_terms: Sequence[object],
    *,
    ignored_tokens: frozenset[str] = frozenset(),
) -> float | None:
    """참고자료 제목이 글 주제와 얼마나 겹치는가 (토큰 최대 일치율).

    Returns None when the reference cannot be judged at all (영문 전용 제목, 기관명과
    일반 단어뿐인 제목, 주제어가 비어 있는 글). 판단 불가는 '무관'이 아니다.
    """
    expanded = [term for term in _expand_related_terms(article_terms) if len(term) >= 2]
    pool = topic_bigram_pool(article_terms)
    if not pool:
        return None
    tokens = _topical_tokens(reference_text, ignored_tokens)
    if not tokens:
        return None
    best = 0.0
    for token in tokens:
        # 합성어 포함 관계('추간판' ⊂ '추간판탈출증')는 bigram 비율이 낮게 나오지만
        # 같은 주제다. 한쪽이 다른 쪽을 통째로 담고 있으면 완전 일치로 본다.
        if any(term in token or token in term for term in expanded):
            return 1.0
        bigrams = char_bigrams(token)
        if not bigrams:
            continue
        best = max(best, len(bigrams & pool) / len(bigrams))
    return best


def find_similar_titles(
    candidate_title: object,
    first_h2: object,
    target_keyword: object,
    existing_titles: Iterable[object],
    threshold: float = DUPLICATE_TITLE_THRESHOLD,
) -> list[tuple[str, float]]:
    """최근 제목 중 후보 글과 사실상 같은 주제인 것들을 (제목, 점수)로 돌려준다.

    판정은 세 가지뿐이다.
      1. 정규화한 제목이 완전히 같다 → 1.0
      2. 제목 bigram Jaccard가 threshold 이상
      3. 같은 핵심 키워드를 쓰면서 첫 H2가 기존 제목과 threshold 이상 겹친다
    """
    normalized_candidate = normalize_topic_text(candidate_title)
    keyword = normalize_topic_text(target_keyword)
    matches: list[tuple[str, float]] = []
    seen: set[str] = set()
    for existing in existing_titles or ():
        title = str(existing or "").strip()
        normalized = normalize_topic_text(title)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized_candidate and normalized == normalized_candidate:
            matches.append((title, 1.0))
            continue
        score = topic_similarity(candidate_title, title)
        if (
            score < threshold
            and keyword
            and keyword in normalized
            and keyword in normalized_candidate
        ):
            score = max(score, topic_similarity(first_h2, title))
        if score >= threshold:
            matches.append((title, round(score, 3)))
    matches.sort(key=lambda match: match[1], reverse=True)
    return matches[:5]
