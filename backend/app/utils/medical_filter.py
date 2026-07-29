"""의료광고 금지 표현 필터 — 전체 경로 공통.

근거: 의료법 제56조, 보건복지부 의료광고 가이드라인 2판(2024.12),
대한의사협회 의료광고심의회 2025.01 신규 심사 사례.
"""
import re
import unicodedata

# Zero-width / 비표시 문자 — 금지 표현 사이에 끼워 넣어 정규식을 회피하는 우회를 차단.
# U+200B ZERO WIDTH SPACE, U+200C ZWNJ, U+200D ZWJ, U+2060 WORD JOINER, U+FEFF BOM, U+00AD SOFT HYPHEN.
_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD], None
)

# 기본 금지 표현 (표시용)
FORBIDDEN_EXPRESSIONS = [
    "1등", "최고", "최우수", "유일", "완치", "100%",
    "성공률", "부작용 없는", "검증된", "가장 잘하는",
    "국내 최초", "세계 최초", "특허", "독보적",
    "노하우", "효과 보장", "전국 유일", "최첨단",
    "안전한 시술", "통증 없는", "흉터 없는",
]

# 정규식 패턴 (변형 포착)
FORBIDDEN_PATTERNS: dict[str, re.Pattern] = {
    "1등": re.compile(r"1등|일등|1위|일위"),
    "최고": re.compile(r"최고[의]?|최상[의]?|으뜸[인]?"),
    "최우수": re.compile(r"최우수|가장\s*우수|제일\s*우수|탁월[한]?"),
    "유일": re.compile(r"유일[한]?|유일무이|전국\s*유일|오직\s*이곳"),
    "완치": re.compile(r"완치[율]?|완전\s*치료|완전\s*회복"),
    "100%": re.compile(r"100\s*%|백\s*퍼센트|100퍼"),
    "성공률": re.compile(r"성공률|성공\s*확률|성공\s*보장"),
    "부작용 없는": re.compile(r"부작용\s*(없|zero|제로|걱정\s*없)"),
    "검증된": re.compile(r"검증[된]?|입증[된]?|확인[된]\s*효과"),
    "가장 잘하는": re.compile(r"가장\s*(잘|뛰어|훌륭)|제일\s*(잘|뛰어)"),
    "국내 최초": re.compile(r"(국내|세계|아시아|전국)\s*최초"),
    "세계 최초": re.compile(r"세계\s*최초"),
    "특허": re.compile(r"특허[를]?\s*(보유|획득|취득|출원|등록)"),
    "독보적": re.compile(r"독보적[인]?|비교\s*불가"),
    # 2025 신규 — 의협 심의회 사례 + GEO 콘텐츠에 자주 새는 표현.
    "노하우": re.compile(r"(저희|우리|병원|원장)[\w가-힣]*\s*만[의]?\s*노하우|차별화된\s*노하우"),
    "효과 보장": re.compile(r"효과[를]?\s*(보장|확실|약속)|보장[된]?\s*효과"),
    "최첨단": re.compile(r"최첨단|첨단[의]?\s*(기술|장비|시술)"),
    "안전한 시술": re.compile(r"안전[한]?\s*(시술|수술|치료)[이가]?\s*보장|100%\s*안전"),
    "통증 없는": re.compile(r"통증\s*없[는이]|무통[증]?[의]?\s*(시술|수술|치료)|아프지\s*않[은는]"),
    "흉터 없는": re.compile(r"흉터\s*(없|zero|제로|걱정\s*없|남지\s*않)"),
}


def normalize_for_check(text: str) -> str:
    """금지 표현 매칭 전 정규화.

    NFKC로 전각 숫자·기호(１００％, １등)를 ASCII로 접고, zero-width 삽입 우회를
    제거한다. 정규화하지 않으면 `100%`/`1등` 패턴이 전각/비표시 변형을 놓친다(MED-1).
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.translate(_ZERO_WIDTH)


def check_forbidden(text: str) -> list[str]:
    """텍스트에서 의료광고 금지 표현을 찾아 매칭된 기본 표현 목록을 반환.

    입력이 **마크다운 본문**이면 이 함수가 아니라 `check_forbidden_markdown`을 써야 한다.
    여기서는 마크업을 해석하지 않으므로 `최**고**의`가 통과한다(의도된 분리 — 아래 참조).
    """
    if not text:
        return []
    normalized = normalize_for_check(text)
    found = []
    for label, pattern in FORBIDDEN_PATTERNS.items():
        if pattern.search(normalized):
            found.append(label)
    return found


# **짝이 맞는** `*` 강조만 제거한다.
#
# CommonMark/GFM에서 `*`는 단어 내부에서도 강조로 동작하므로 `최**고**의`는
# `최<strong>고</strong>의`로 렌더되어 환자에게는 "최고의"로 보인다. 이것이 우회의 실체다.
#
# 반대로 아래는 렌더러가 **리터럴로 보여주므로 건드리면 안 된다** — 지우면 화면에 없는
# 위반을 발명하게 되고, 오탐은 공개 표면에서 병원 정보를 삭제하는 부작용까지 낸다:
#   - `_` : intraword 강조가 아니라 `최_고`는 밑줄이 그대로 보인다
#   - `~` : remarkGfm가 singleTilde:false라 리터럴
#   - `<...>` : rehypeRaw 미설치라 이스케이프되어 보인다
#   - 코드 스팬 내부 : 문장부호가 그대로 렌더된다
#   - 짝 없는 `*` (`5*3`, 목록 표지 `* 항목`) : 리터럴
#
# 여는 구분자 뒤와 닫는 구분자 앞이 공백이 아니어야 강조가 성립한다(CommonMark 규칙).
# 한 줄 안에서만 매칭한다 — 빈 줄은 강조를 끊고, 줄을 넘겨 단어가 붙는 것을 막는다.
_PAIRED_EMPHASIS = re.compile(r"(\*{1,3})([^\s*](?:[^*]*[^\s*])?)\1")

# `_`는 **단어 경계에서만** 강조로 동작한다(CommonMark). 그래서 두 경우를 갈라야 한다:
#   `부작용 _없는_ 시술` → 강조 → 화면엔 "부작용 없는 시술"  (제거 대상)
#   `최_고의 진료`       → intraword → 밑줄이 그대로 보인다   (건드리면 오탐)
_PAIRED_UNDERSCORE = re.compile(
    r"(?<![0-9A-Za-z가-힣])(_{1,3})([^\s_](?:[^_]*[^\s_])?)\1(?![0-9A-Za-z가-힣])"
)
# remarkGfm의 취소선. singleTilde:false는 홑물결(`~`)만 끄고 `~~...~~`는 유효하다.
# `최~~고~~의 진료` → `최<del>고</del>의` → 화면엔 "최고의 진료".
_PAIRED_STRIKETHROUGH = re.compile(r"(~~)([^\s~](?:[^~]*[^\s~])?)\1")

# 코드 영역: **구분자(백틱/펜스)는 보이지 않고 내용만 보인다.**
# 따라서 구분자는 없애고 내용은 그대로 남긴다. 내용 안의 `*`는 화면에 실제로 보이므로
# 강조 제거 대상이 아니다. (구분자를 남기면 `최`고`의`가 화면엔 "최고의"인데 검사에서
# 걸리지 않고, 내용의 별표를 지우면 코드 안의 리터럴 별표가 사라져 오탐이 난다.)
_FENCED_CODE = re.compile(r"^```[^\n]*\n(.*?)^```[^\n]*$", re.DOTALL | re.MULTILINE)
_CODE_SPAN = re.compile(r"`+([^`]*)`+")

# 링크 목적지는 화면에 보이지 않는다 — 표시 텍스트만 남긴다.
# `[안내](https://x/최**고**)`는 "안내"만 보이므로 목적지를 검사하면 오탐이 난다.
#
# 단, **CommonMark 링크일 때만** 그렇다. 목적지에 이스케이프되지 않은 공백이 있으면
# 링크가 아니라 대괄호·소괄호가 그대로 보이는 일반 텍스트다:
#   `[개인차 있음](통증 없는 경우도 있습니다)` → 화면에 "통증 없는"이 그대로 노출된다.
# 목적지를 느슨하게 잡으면 이런 텍스트를 지워 **미탐**이 되므로, 공백 없는 목적지
# (또는 <...> 형태)와 선택적 title만 링크로 인정한다.
_INLINE_LINK = re.compile(
    r"\[([^\]]*)\]\(\s*(?:<[^>\n]*>|[^\s()]*)"
    r"""(?:\s+"[^"\n]*"|\s+'[^'\n]*'|\s+\([^)\n]*\))?\s*\)"""
)


def _strip_paired_emphasis(line: str) -> str:
    """한 줄에서 화면에 보이지 않는 강조 구분자를 벗긴다(중첩 대응).

    `*`(단어 내부 포함), 단어 경계의 `_`, 그리고 `~~` 취소선 — 셋 다 렌더되면 사라진다.
    """
    for _ in range(4):  # ***중첩*** 정도까지. 무한 루프 방지용 상한.
        stripped = _PAIRED_EMPHASIS.sub(r"\2", line)
        stripped = _PAIRED_UNDERSCORE.sub(r"\2", stripped)
        stripped = _PAIRED_STRIKETHROUGH.sub(r"\2", stripped)
        if stripped == line:
            return line
        line = stripped
    return line


def markdown_visible_text(markdown_text: str) -> str:
    """마크다운 본문에서 **환자에게 실제로 보이는 텍스트**에 가깝게 되돌린다.

    검사 시점 텍스트와 노출 시점 텍스트가 다르면 발행 게이트는 무의미하다.
    공개 표면(`site/app/[slug]/contents/[contentId]/page.tsx`)이 ReactMarkdown +
    remarkGfm로 렌더하므로, 강조 구분자는 화면에서 사라진다.

    보수적으로 동작한다 — 렌더러가 실제로 지우는 것만 지운다:
      - 짝이 맞는 `*` 강조 구분자
      - 코드 펜스/백틱 **구분자**(내용은 보이므로 그대로 둔다)
      - 인라인 링크의 목적지(표시 텍스트만 보인다)
    리터럴로 남는 것(`_`, `~`, `<...>`, 코드 내용의 `*`)은 건드리지 않는다.
    줄바꿈도 보존한다(블록 경계를 넘어 단어가 붙는 것을 막기 위해).

    **입력은 마크다운 본문(body)만이어야 한다.** 제목·메타·FAQ는 공개 표면에서
    React children으로 리터럴 렌더되므로 이 함수를 적용하면 오탐이 난다.
    필드별로 올바른 검사기를 고르려면 `check_forbidden_content_fields`를 쓸 것.
    """
    if not markdown_text:
        return ""

    def _visible_outside_code(segment: str) -> str:
        # 링크 목적지를 먼저 걷어낸 뒤(목적지 안의 `*`가 강조로 오인되지 않도록),
        # 줄 단위로 강조를 벗긴다 — 강조가 블록 경계를 넘어 매칭되지 않게.
        segment = _INLINE_LINK.sub(r"\1", segment)
        return "\n".join(_strip_paired_emphasis(line) for line in segment.split("\n"))

    def _split_on(pattern, text: str) -> str:
        out: list[str] = []
        last = 0
        for span in pattern.finditer(text):
            out.append(_visible_outside_code(text[last : span.start()]))
            # 구분자는 화면에 없고 내용만 보인다 → 내용을 그대로 남긴다.
            out.append(span.group(1))
            last = span.end()
        out.append(_visible_outside_code(text[last:]))
        return "".join(out)

    # 펜스 블록을 먼저 떼어낸다 — 여러 줄이라 인라인 백틱 규칙으로는 못 잡는다.
    out: list[str] = []
    last = 0
    for fence in _FENCED_CODE.finditer(markdown_text):
        out.append(_split_on(_CODE_SPAN, markdown_text[last : fence.start()]))
        out.append(fence.group(1))  # 펜스 내용은 별표까지 그대로 보인다
        last = fence.end()
    out.append(_split_on(_CODE_SPAN, markdown_text[last:]))
    return "".join(out)


def check_forbidden_markdown(markdown_text: str) -> list[str]:
    """마크다운 본문을 **렌더 결과 기준**으로 검사한다.

    `check_forbidden`과 분리해 둔 이유: 공유 필터는 병원 소개문·진료 항목 등
    마크다운이 아닌 평문 직렬화 경로에서도 쓰이며(`api/public/site.py`), 거기에
    마크업 해석을 적용하면 오탐이 병원 정보를 공개 표면에서 삭제한다.
    마크다운이 입력인 게 확실한 곳(생성 검증·발행 게이트)에서만 이 함수를 쓴다.
    """
    if not markdown_text:
        return []
    return check_forbidden(markdown_visible_text(markdown_text))


# 공개 표면에서 **마크다운으로 렌더되는** 필드. 나머지(title/meta_description/faq_*)는
# React children으로 리터럴 렌더된다 — site/app/[slug]/contents/[contentId]/page.tsx에서
# 본문만 ReactMarkdown을 탄다. 이 구분이 틀리면 검사도 틀린다.
MARKDOWN_RENDERED_FIELDS = frozenset({"body"})


def check_forbidden_content_fields(values: dict, fields: tuple[str, ...]) -> list[str]:
    """콘텐츠 필드들을 **필드별로 올바른 기준**으로 검사한다.

    필드를 합쳐서 한 번에 마크다운으로 해석하면 양방향으로 틀린다:
      - 제목 `최*고*`는 화면에 별표가 그대로 보이는데 위반으로 오탐된다
      - 제목 끝 백틱과 본문 백틱이 합쳐져 코드 스팬이 **합성**되면 본문 위반이 은폐된다
      - 필드 경계를 넘어 `최*고` + `의*`가 붙어 없던 강조 쌍이 생긴다
    그래서 본문만 렌더 기준으로, 나머지는 평문 기준으로 각각 검사한 뒤 합친다.

    `fields`는 호출부가 넘긴다(생성 엔진의 FORBIDDEN_CHECK_FIELDS) — 여기서 import하면
    content_engine ↔ medical_filter 순환 참조가 된다.
    """
    found: list[str] = []
    for field in fields:
        text = values.get(field)
        if not isinstance(text, str) or not text:
            continue
        if field in MARKDOWN_RENDERED_FIELDS:
            found.extend(check_forbidden_markdown(text))
        else:
            found.extend(check_forbidden(text))
    return list(dict.fromkeys(found))
