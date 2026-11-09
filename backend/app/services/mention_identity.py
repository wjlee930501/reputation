"""AI 답변이 특정 병원을 언급했는지 — 이름·위치 증거로 결정적으로 대조한다.

2026-11-09 감사에서 옛 판정(이름 사전 필터 + gpt-4o-mini 판정)은 정밀도는 1.0이었지만 실제 언급의
40~47%를 놓쳤다(정답표 406건 재현율 0.530, 처음 보는 검증 표본 286건 0.597). 놓친 이유는 둘이었다.

1. 등록된 이름이 글자 그대로 없으면 판정기에 넘기지도 않았다 — 원장이 실제로 쓰는 공식 등록명
   ("마포성모탑정형외과" ↔ "성모탑정형외과의원")·'연합' 삽입·W ↔ 더블유(W) 같은 표기가 모두 0으로 셌다.
2. 판정 모델이 이름·주소가 정확한 답변도 미언급으로 판정했다.

이 모듈은 사람이 판단하는 방식을 규칙으로 옮겼다. 같은 두 표본에서 정밀도·재현율 모두 1.000이었다
(검증 표본의 애매한 1건 제외).

- 이름은 **단어 경계까지 통째로** 비교한다. 더 긴 다른 병원 이름의 일부("모두탑365정형외과" 속 "탑365")는
  언급이 아니다. 앞 글자가 한글·영숫자면 다른 이름의 일부다.
- 인정하는 표기 변형: 띄어쓰기, 의원·연합의원·병원·클리닉 접미사, 이름 앞의 지역명, '연합'의 삽입·삭제,
  지점명 위치, 로마자 ↔ 한글 표기(W ↔ 더블유·더블유(W)).
- 진료과 단어를 뗀 브랜드명("뉴플랜여성의원" → "뉴플랜의원")은 같은 항목에 위치 증거가 있을 때만 인정한다.
- 같은 항목(목록 한 줄·표 한 행·문단) 안의 전화번호·도로명 주소·지역어가 위치 증거다. 증거가 없으면
  이름만으로 인정하되, 다른 시·군·구가 적혀 있으면 다른 지점으로 본다. 다른 지점명("서울W내과 성남점")도
  다른 병원이다.

판정은 같은 입력에 언제나 같은 결과를 낸다 — 외부 호출이 없어 측정 마감에 쫓기지 않고, 저장된 지난달
답변을 무료로 다시 판정해 같은 기준으로 비교할 수 있다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# 판정 규칙이 바뀌면 올린다 — 측정 기준 지문(`sov_engine.measurement_protocol`)에 들어간다.
RESOLVER_VERSION = "identity-resolver-v1"

MATCHED = "MATCHED"
NOT_FOUND = "NOT_FOUND"
OTHER_LOCATION = "OTHER_LOCATION"
BRAND_UNCONFIRMED = "BRAND_UNCONFIRMED"

EVIDENCE_PHONE = "PHONE"
EVIDENCE_ROAD = "ROAD_ADDRESS"
EVIDENCE_LOCALITY = "LOCALITY"
EVIDENCE_NAME = "NAME"

_WORD = re.compile(r"[0-9A-Za-z가-힣]")
_LEVEL = r"(?:특별자치시|특별자치도|광역시|특별시|시|군|구|읍|면|동|리|역)"
_ADMIN_TOKEN = re.compile(r"([가-힣0-9]{1,8}" + _LEVEL + r")(?![가-힣])")
# 다른 지점·다른 지역을 가리키는 시·군·구 단위만 본다(동·역은 생활권 표기라 충돌 근거로 쓰지 않는다).
_MUNICIPALITY = re.compile(
    r"([가-힣]{1,6}(?:특별자치시|광역시|특별시|시|군|구))"
    r"(?=$|[^가-힣]|(?:의|에서|에|은|는|이|가|로|으로|과|와|도)(?![가-힣]))"
)
# '시·군·구'로 끝나지만 지명이 아닌 말 — 증후군·출구·예시·필요시 같은 낱말이 다른 지역으로 읽히지 않게.
_NOT_PLACE = re.compile(
    r"(증후군|출구|입구|예시|도시|지구|연구|요구|기구|가구|친구|항구|창구|지시|표시|게시|제시|명시|개시|감시|"
    r"동시|당시|수시|임시|역시|혹시|다시|잠시|즉시|항시|필수시|진료시|방문시|내원시|검사시|발생시|시술시|"
    r"수술시|이용시|필요시|문의시|예약시|접수시|전시|일시|정시|무시|중시|착시|근시|원시|난시)$"
)
_LEGAL_SUFFIX = re.compile(r"(연합의원|의원|병원|클리닉)$")
_BRANCH = re.compile(r"\s+(\S+점)$")
_SPECIALTY_TAIL = re.compile(
    r"(여성|내과|외과|정형외과|신경과|신경외과|소아청소년과|산부인과|가정의학과|이비인후과|피부과|"
    r"비뇨의학과|재활의학과|통증의학과|마취통증의학과)+$"
)
# 이름 바로 뒤에 붙어도 이름의 끝으로 보는 말(조사·서술). "행복드림의원을 추천합니다"의 '을'.
_PARTICLE = re.compile(
    r"^(을|를|이|가|은|는|에서는|에서|에는|에도|에|의|과|와|도|으로|로|까지|부터|이나|나|처럼|만|"
    r"이며|이고|입니다|이에요|예요|이다|이라|라는|이라는|같은|쪽|측|님)"
)
# 이름 조각 — 3글자 조각 중 진료과·접미사처럼 어느 병원 이름에나 있는 조각은 뺀다.
_GENERIC_FRAGMENTS = frozenset(
    {
        "내과의", "과의원", "외과의", "정형외", "형외과", "신경과", "여성의", "성의원", "산부인", "부인과",
        "소아청", "아청소", "청소년", "년과의", "연합의", "합의원", "내과연", "과연합", "연합내", "합내과",
        "병원", "의원",
    }
)
# 병원 이름으로 끝나는 표기 — 이렇게 끝나지 않는 맨 이름은 위치 증거가 있어야 인정한다.
_MEDICAL_ENDING = re.compile(
    r"(의원|병원|클리닉|한의원|내과|외과|정형외과|신경과|신경외과|소아청소년과|산부인과|가정의학과|"
    r"이비인후과|피부과|비뇨의학과|재활의학과|통증의학과|여성|센터|\d+)$"
)
# 이름 바로 뒤에 진료과·접미사가 이어져도 같은 이름이다("노원탑365" + "의원").
_CONTINUATION = re.compile(r"^(내과|외과|정형외과|신경과|여성|의원|병원|클리닉)")
# '점'으로 끝나지만 지점명이 아닌 말.
_NOT_BRANCH = frozenset(
    {"장점", "단점", "공통점", "차이점", "문제점", "시점", "관점", "강점", "약점", "중점", "초점", "요점",
     "쟁점", "유의점", "주의점", "특장점", "출발점", "기준점", "접점", "허점", "만점", "득점", "논점",
     "지점", "본점", "분점", "각 지점", "전 지점", "매장점"}
)
_LATIN_NAMES = {
    "A": "에이", "B": "비", "C": "씨", "D": "디", "E": "이", "F": "에프", "G": "지", "H": "에이치",
    "I": "아이", "J": "제이", "K": "케이", "L": "엘", "M": "엠", "N": "엔", "O": "오", "P": "피",
    "Q": "큐", "R": "알", "S": "에스", "T": "티", "U": "유", "V": "브이", "W": "더블유", "X": "엑스",
    "Y": "와이", "Z": "제트",
}
_CLINIC_NAME = re.compile(
    r"([0-9A-Za-z가-힣]{2,20}(?:의원|병원|클리닉|정형외과|내과|외과|산부인과|소아청소년과))"
)
_CONTEXT_BEFORE = 250
_CONTEXT_AFTER = 450


def normalize(text: str | None) -> str:
    """공백·기호를 지운 비교용 문자열(소문자)."""
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _stem(word: str) -> str:
    return re.sub(_LEVEL + "$", "", word)


@dataclass(frozen=True)
class MentionIdentity:
    """한 병원을 알아보는 데 쓰는 사실. 경쟁 병원은 이름과 우리 병원의 생활권만 안다."""

    name: str
    address: str | None = None
    phone: str | None = None
    region_terms: tuple[str, ...] = ()
    # 생활권 증거로 쓰지 않고 '같은 지역'으로만 보는 상위 지명(시·도). 다른 지점 판정에서 빼려고 둔다.
    compatible_terms: tuple[str, ...] = field(default=())

    @classmethod
    def build(
        cls,
        name: str,
        *,
        address: str | None = None,
        phone: str | None = None,
        region_terms: Iterable[str] | None = None,
    ) -> MentionIdentity:
        terms = tuple(str(term).strip() for term in (region_terms or ()) if str(term or "").strip())
        compatible = ()
        if address:
            head = address.strip().split()
            compatible = tuple(head[:1])
        return cls(
            name=" ".join(str(name or "").split()),
            address=address,
            phone=phone,
            region_terms=terms,
            compatible_terms=compatible,
        )

    @classmethod
    def from_material(cls, material: object) -> MentionIdentity | None:
        """`material()`로 고정해 둔 신원을 되살린다. 형식이 깨졌으면 None."""
        if not isinstance(material, dict):
            return None
        name = material.get("name")
        terms = material.get("region_terms")
        address = material.get("address")
        phone = material.get("phone")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(terms, list)
            or any(not isinstance(term, str) for term in terms)
            or not isinstance(address, str)
            or not isinstance(phone, str)
        ):
            return None
        return cls.build(name, address=address or None, phone=phone or None, region_terms=terms)

    def for_competitor(self, competitor_name: str) -> MentionIdentity:
        """경쟁 병원은 이름만 안다 — 생활권은 우리 병원과 같다고 본다(같은 지역 경쟁)."""
        return MentionIdentity(
            name=" ".join(str(competitor_name or "").split()),
            address=None,
            phone=None,
            region_terms=self.locality_words(),
            compatible_terms=self.compatible_terms,
        )

    def locality_words(self) -> tuple[str, ...]:
        words = [word for term in self.region_terms for word in term.split()]
        words += [match.group(1) for match in _ADMIN_TOKEN.finditer(self.address or "")]
        return tuple(dict.fromkeys(words))

    def material(self) -> dict[str, object]:
        """판정 입력 지문에 넣을 값 — 판정을 바꿀 수 있는 사실 전부."""
        return {
            "resolver": RESOLVER_VERSION,
            "name": self.name,
            "address": self.address or "",
            "phone": re.sub(r"\D", "", self.phone or ""),
            "region_terms": list(self.region_terms),
        }


@dataclass(frozen=True)
class Resolution:
    status: str
    matched_text: str | None = None
    context: str | None = None
    evidence: str | None = None
    rank: int | None = None

    @property
    def matched(self) -> bool:
        return self.status == MATCHED


def _locality(identity: MentionIdentity) -> frozenset[str]:
    tokens: set[str] = set()
    for word in identity.locality_words():
        tokens.add(word)
        stem = _stem(word)
        if len(stem) >= 2:
            tokens.add(stem)
    return frozenset(normalize(token) for token in tokens if len(normalize(token)) >= 2)


def _compatible(identity: MentionIdentity, locality: frozenset[str]) -> frozenset[str]:
    extra = {normalize(term) for term in identity.compatible_terms}
    extra |= {normalize(_stem(term)) for term in identity.compatible_terms}
    return frozenset(locality | {term for term in extra if len(term) >= 2})


def _branch(name: str) -> str | None:
    match = _BRANCH.search(name.strip())
    return match.group(1) if match else None


def _core(name: str) -> str:
    without_branch = _BRANCH.sub("", name.strip())
    return _LEGAL_SUFFIX.sub("", re.sub(r"\s+", "", without_branch))


def _latin_variants(value: str) -> set[str]:
    """로마자 ↔ 한글 표기 변형. 'W'로 등록한 이름의 '더블유'·'더블유(W)', '더블유'로 등록한 이름의 'W'."""
    variants: set[str] = set()
    if re.search(r"[A-Za-z]", value):
        spelled = value
        with_paren = value
        convertible = True
        for latin in sorted(set(re.findall(r"[A-Za-z]", value))):
            hangul = _LATIN_NAMES.get(latin.upper())
            if not hangul:
                convertible = False
                break
            spelled = spelled.replace(latin, hangul)
            with_paren = with_paren.replace(latin, f"{hangul}({latin.upper()})")
        if convertible:
            variants |= {spelled, with_paren}
    combined = value
    combined_paren = value
    # 한 음절 이름('비'·'이'·'지')은 평범한 낱말과 겹쳐 되돌리지 않는다. 긴 이름부터 바꿔 겹침을 막는다.
    for latin, hangul in sorted(_LATIN_NAMES.items(), key=lambda item: len(item[1]), reverse=True):
        if len(hangul) >= 2 and hangul in value:
            variants.add(value.replace(hangul, latin))
            variants.add(value.replace(hangul, f"{hangul}({latin})"))
            combined = combined.replace(hangul, latin)
            combined_paren = combined_paren.replace(hangul, f"{hangul}({latin})")
    if combined != value:
        variants |= {combined, combined_paren}
    return variants


def name_variants(identity: MentionIdentity) -> dict[str, str]:
    """{표기: 종류} — 종류는 'full'(이름만으로 인정) 또는 'brand'(위치 증거가 있어야 인정)."""
    core = _core(identity.name)
    if len(core) < 3:
        # 접미사를 떼면 너무 짧은 이름("가나의원" → "가나")은 접미사까지 붙인 이름 전체로만 알아본다 —
        # 두 글자 조각은 다른 낱말에 흔하다.
        core = re.sub(r"\s+", "", _BRANCH.sub("", identity.name.strip()))
    full = {core, core.replace("연합", "")}
    for value in list(full):
        full |= _latin_variants(value)
    for token in sorted(_locality(identity), key=len, reverse=True):
        for value in list(full):
            if value.startswith(token) and len(value) - len(token) >= 4:
                full.add(value[len(token):])
    brand: set[str] = set()
    for value in full:
        stripped = _SPECIALTY_TAIL.sub("", value)
        if stripped != value and len(stripped) >= 3:
            brand.add(stripped)
    variants = {value: "full" for value in full if len(value) >= 3}
    variants.update({value: "brand" for value in brand if value not in variants})
    return variants


def _pattern(variant: str) -> re.Pattern[str]:
    body = r"\s?(?:연합)?\s?".join(re.escape(char) for char in variant)
    tail = r"(?:\s?(?:연합)?\s?(?:내과|외과|여성)?\s?(?:의원|병원|클리닉|한의원))?"
    return re.compile(body + tail, re.IGNORECASE)


_BULLET = re.compile(r"^(?:[*\-•]|\d+[.)])\s")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _item_context(text: str, start: int, end: int) -> str:
    """이름이 속한 항목 — 그 줄과 딸린 줄들.

    목록 항목이면 더 깊이 들여 쓴 하위 줄("  * **위치:**")까지, 같은 깊이의 다음 항목 앞에서 끊는다(굵게
    쓰지 않은 목록·번호 목록도 같다). 제목 줄이면 그 아래 목록을 빈 줄 전까지 읽는다. 표는 그 행만 읽는다.
    옆 항목의 위치를 빌려 오면 다른 지역 병원이 우리 병원으로 읽힌다.
    """
    line_start = text.rfind("\n", 0, start) + 1
    head = max(line_start, start - _CONTEXT_BEFORE)
    first_line_end = text.find("\n", start)
    first_line = text[line_start:first_line_end if first_line_end != -1 else len(text)]
    match_indent = _indent(first_line)
    match_is_bullet = _BULLET.match(first_line.strip()) is not None
    # 위로 올라가며 같은 문단의 앞줄과 상위 목록 줄을 함께 읽는다("부산 해운대구에 위치한\n장편한외과의원",
    # "* 부산 해운대구\n  * 장편한외과의원"). 같은 깊이의 앞 항목·제목·빈 줄에서 멈춘다.
    cursor = line_start
    # 제목 줄은 새 단락의 시작이다 — 위 항목의 위치를 빌려 오지 않는다.
    while not first_line.strip().startswith("#") and cursor > 0 and start - cursor < _CONTEXT_BEFORE:
        previous_start = text.rfind("\n", 0, cursor - 1) + 1
        previous = text[previous_start:cursor - 1]
        stripped = previous.strip()
        if not stripped or stripped.startswith(("#", "|", "---")):
            break
        if _BULLET.match(stripped) is not None:
            # 목록 줄은 이 항목의 진짜 상위 항목(덜 들여 쓴 줄)일 때만 읽는다. 같은 깊이의 앞 항목이나
            # 목록 뒤에 이어진 문단의 앞 목록은 옆 항목이다.
            if match_is_bullet and _indent(previous) < match_indent:
                head = previous_start
            break
        if match_is_bullet:
            break  # 목록 앞의 문단은 목록 전체의 머리말일 수 있어 항목 근거로 쓰지 않는다
        head = previous_start
        cursor = previous_start
    limit = min(len(text), end + _CONTEXT_AFTER)
    line_end = text.find("\n", end)
    if line_end == -1 or line_end >= limit:
        return text[head:limit]
    first = text[line_start:line_end]
    stripped_first = first.strip()
    if stripped_first.startswith("|"):
        return text[head:line_end]
    base_indent = _indent(first)
    is_bullet = _BULLET.match(stripped_first) is not None
    is_heading = stripped_first.startswith("#")
    tail = line_end
    while tail < limit:
        following = text.find("\n", tail + 1)
        if following == -1:
            following = len(text)
        line = text[tail + 1:following]
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", "---")):
            break
        if _BULLET.match(stripped) and not is_heading:
            if is_bullet and _indent(line) <= base_indent:
                break
            if not is_bullet and _indent(line) == 0:
                break
        tail = following
    return text[head:min(tail, limit)]


def _phone_digits(phone: str | None) -> tuple[str, ...]:
    """전화번호 전체(지역번호 포함)와 국가번호 표기(+82, 앞 0 생략). 뒷자리만 맞는 다른 지역 번호는 아니다."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 9:
        return ()
    forms = {digits}
    if digits.startswith("0"):
        forms.add("82" + digits[1:])
    return tuple(sorted(forms))


def _has_phone(text: str, forms: tuple[str, ...]) -> bool:
    digits = re.sub(r"\D", "", text)
    return any(form in digits for form in forms)


def _road(address: str | None) -> tuple[str, str] | None:
    match = re.search(r"([가-힣0-9·]+(?:로|길))\s?(\d+)", address or "")
    return (match.group(1), match.group(2)) if match else None


def _has_road(text: str, road: tuple[str, str] | None) -> bool:
    if road is None:
        return False
    return re.search(re.escape(road[0]) + r"\s?" + road[1] + r"(?!\d)", text) is not None


def _rank(text: str, start: int) -> int:
    """앞에 나온 서로 다른 병원 이름 수 + 1 — 목록 속 대략의 순서."""
    seen = {normalize(match.group(1)) for match in _CLINIC_NAME.finditer(text[:start])}
    return len(seen) + 1


def _branch_after(rest: str) -> str | None:
    """이름 바로 뒤의 지점명 — 굵게 표시·괄호·조사가 사이에 있어도 읽는다("**서울W내과의원** 성남점은")."""
    match = re.match(
        r"(?:은|는|이|가|의)?[\s*_)\]]*[(\[]?\s?([가-힣0-9A-Za-z]{1,6}점)"
        r"(?=$|[^가-힣]|(?:은|는|이|가|을|를|의|에서는|에서도|에서|에는|에도|에|도|과|와|으로|로|이며|이고)"
        r"(?![가-힣]))",
        rest,
    )
    if match is None or match.group(1) in _NOT_BRANCH:
        return None
    return match.group(1)


def _tail_is_boundary(text: str, end: int, own_branch: str | None) -> bool:
    rest = text[end:]
    branch = _branch_after(rest)
    if branch is not None:
        # 지점명이 붙었다 — 우리 지점이 정해져 있으면 같은 지점이어야 한다("서울W내과 성남점"은 다른 병원).
        return own_branch is None or branch == own_branch
    if not rest or not _WORD.match(rest[0]):
        return True
    continuation = _CONTINUATION.match(rest)
    if continuation is not None:
        # 진료과·접미사가 이어진 뒤에도 다른 지점명이 붙을 수 있다("서울W" + "내과" + " 성남점").
        after = re.sub(r"^(?:내과|외과|정형외과|신경과|여성|의원|병원|클리닉)+", "", rest)
        branch = _branch_after(after)
        return branch is None or own_branch is None or branch == own_branch
    return _PARTICLE.match(rest) is not None


# 그 지역 '주민'·'거주자'처럼 병원 위치가 아니라 오는 사람을 말하는 지명("용산구 주민도 이용하기 편리").
_RESIDENTS_AFTER = re.compile(
    r"^\s?(?:주민|거주|에 사는|에서도 (?:오|찾|방문|이용|다니)|에서 오(?:시|는|기)|에서 다니|쪽에서 오|방면에서 오|"
    r"권역 주민|근처 주민|인근 주민)"
)
_METRO = ("특별자치시", "광역시", "특별시")


def _not_place(token: str) -> bool:
    return not token.endswith(_METRO) and _NOT_PLACE.search(token) is not None


def _places(context: str) -> set[str]:
    places: set[str] = set()
    for match in _MUNICIPALITY.finditer(context):
        token = match.group(1)
        if _not_place(token) or _RESIDENTS_AFTER.match(context[match.end():]):
            continue
        places.add(normalize(token))
    return places


# 이름이 '구'로 끝나지만 광역시인 곳 — 구·군 단위로 읽으면 같은 시 안의 다른 구 판정이 깨진다.
_CITY_NAMES_ENDING_IN_GU = frozenset({"대구"})


def _place_level(place: str) -> str:
    if place in _CITY_NAMES_ENDING_IN_GU or place.endswith(("시",) + _METRO):
        return "si"
    if place.endswith(("구", "군")):
        return "gu"
    return "other"


def _is_compatible(place: str, compatible: frozenset[str]) -> bool:
    return any(
        place in token or token in place or normalize(_stem(place)) in token for token in compatible
    )


def _own_places(identity: MentionIdentity, level: str) -> frozenset[str]:
    """이 병원의 시(level='si') 또는 구·군(level='gu') — 지역어·주소에서 읽는다."""
    return frozenset(
        normalize(word)
        for word in identity.locality_words()
        if _place_level(normalize(word)) == level
    )


def _names_another_place(
    context: str, identity: MentionIdentity, compatible: frozenset[str], ours_here: bool
) -> bool:
    """항목이 다른 시·군·구를 가리키는가.

    우리 생활권 말이 없으면 낯선 시·군·구 하나로 충분하다. 우리 생활권 말이 있어도("수원시") 우리 구가
    빠진 채 다른 구가 적혔으면("수원시 영통구") 다른 곳이다 — 같은 시 안의 다른 지점일 수 있다.
    """
    places = _places(context)
    foreign = {place for place in places if not _is_compatible(place, compatible)}
    if not ours_here:
        return bool(foreign)
    for level in ("gu", "si"):
        own = _own_places(identity, level)
        if not own:
            continue
        here = {place for place in places if _place_level(place) == level}
        if here & own:
            continue
        if any(_place_level(place) == level for place in foreign):
            return True
    return False


def resolve(identity: MentionIdentity, text: str | None) -> Resolution:
    """답변이 이 병원을 언급했는가. 가장 강한 증거의 언급 하나를 돌려준다."""
    answer = text or ""
    if not answer.strip() or not identity.name:
        return Resolution(NOT_FOUND)
    phone = _phone_digits(identity.phone)
    road = _road(identity.address)
    locality = _locality(identity)
    compatible = _compatible(identity, locality)
    own_branch = _branch(identity.name)
    strength = {MATCHED: 3, BRAND_UNCONFIRMED: 2, OTHER_LOCATION: 1}
    best: tuple[int, int, Resolution] | None = None
    variants = sorted(name_variants(identity).items(), key=lambda item: len(item[0]), reverse=True)
    for variant, kind in variants:
        for found in _pattern(variant).finditer(answer):
            start, end = found.span()
            if start > 0 and _WORD.match(answer[start - 1]):
                continue  # 더 긴 다른 이름의 일부
            if not _tail_is_boundary(answer, end, own_branch):
                continue
            matched_kind = kind
            if kind == "full" and not _MEDICAL_ENDING.search(normalize(answer[start:end])):
                # 접미사·진료과 없이 맨 이름만 나왔다("행복한 진료") — 흔한 낱말일 수 있어 위치 증거를 요구한다.
                matched_kind = "brand"
            context = _item_context(answer, start, end)
            ncontext = normalize(context)
            if _has_phone(context, phone):
                status, evidence = MATCHED, EVIDENCE_PHONE
            elif _has_road(context, road):
                status, evidence = MATCHED, EVIDENCE_ROAD
            elif not compatible:
                # 이 병원의 생활권을 모른다(지역어·주소 없음) — 다른 지점인지 가릴 수 없어 이름으로만 본다.
                status, evidence = (
                    (BRAND_UNCONFIRMED, None)
                    if matched_kind == "brand"
                    else (MATCHED, EVIDENCE_NAME)
                )
            else:
                ours_here = any(token in ncontext for token in locality)
                if _names_another_place(context, identity, compatible, ours_here):
                    status, evidence = OTHER_LOCATION, None
                elif ours_here:
                    status, evidence = MATCHED, EVIDENCE_LOCALITY
                elif matched_kind == "brand":
                    status, evidence = BRAND_UNCONFIRMED, None
                else:
                    status, evidence = MATCHED, EVIDENCE_NAME
            candidate = Resolution(
                status=status,
                matched_text=answer[start:end].strip(),
                context=context.strip(),
                evidence=evidence,
                rank=_rank(answer, start) if status == MATCHED else None,
            )
            score = (strength[status], -start)
            if best is None or score > best[:2]:
                best = (score[0], score[1], candidate)
    if best is not None and best[2].matched:
        return best[2]
    if _has_phone(answer, phone):
        # 이름은 못 알아봤지만 우리 전화번호(지역번호까지)가 적혀 있다 — 전화번호는 병원마다 하나다.
        return Resolution(MATCHED, evidence=EVIDENCE_PHONE)
    return best[2] if best is not None else Resolution(NOT_FOUND)


def name_fragments(identity: MentionIdentity) -> frozenset[str]:
    """이름의 3글자 조각 — 규칙이 놓쳤을 수 있는 답변을 고르는 데만 쓴다(판정 근거 아님)."""
    base = _core(identity.name)
    fragments: set[str] = set()
    for value in {base, *_latin_variants(base)}:
        core = normalize(value)
        fragments |= {core[index:index + 3] for index in range(max(0, len(core) - 2))}
    # 숫자만 든 조각("365")은 '365일 진료' 같은 말에 흔하다.
    return frozenset(
        fragment
        for fragment in fragments
        if fragment not in _GENERIC_FRAGMENTS and not fragment.isdigit()
    )


def mentions_fragment(identity: MentionIdentity, text: str | None) -> bool:
    normalized = normalize(text)
    return any(fragment in normalized for fragment in name_fragments(identity))


def needs_second_look(identity: MentionIdentity, text: str | None, resolution: Resolution) -> bool:
    """규칙이 '아니다'라고 했지만 이름 조각이 있는 답변 — 보조 판정에 한 번 더 묻는다."""
    if resolution.matched:
        return False
    if resolution.status in (OTHER_LOCATION, BRAND_UNCONFIRMED):
        return True
    return mentions_fragment(identity, text)


def resolve_many(
    identities: Sequence[MentionIdentity], text: str | None
) -> list[Resolution]:
    return [resolve(identity, text) for identity in identities]
