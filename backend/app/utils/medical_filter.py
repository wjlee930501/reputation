"""의료광고 금지 표현 필터 — 전체 경로 공통.

근거: 의료법 제56조, 보건복지부 의료광고 가이드라인 2판(2024.12),
대한의사협회 의료광고심의회 2025.01 신규 심사 사례.
"""
import re
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

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
    # "1등급"은 질환 중증도·화상 분류 같은 임상 등급 표기다. 광고 최상급이 아니므로
    # 매칭 자체에서 제외한다("1등급 화상"). 순위 주장("1등 병원")은 그대로 막는다.
    "1등": re.compile(r"1등(?!급)|일등(?!급)|1위|일위"),
    # 최고혈압(수축기혈압)은 의학 용어다. 최상/으뜸 및 "최고의 진료" 같은
    # 광고 최상급은 그대로 막되 혈압 문맥만 제외한다.
    "최고": re.compile(
        r"최고(?!(?:\s*[/·]\s*최저)?\s*(?:혈압|수축기|이완기))[의]?"
        r"|최상[의]?|으뜸[인]?"
    ),
    "최우수": re.compile(r"최우수|가장\s*우수|제일\s*우수|탁월[한]?"),
    # "유일한 치료법은 아닙니다"처럼 독점 주장 자체를 바로 부정하는 문장만
    # 허용한다. 뒤쪽의 무관한 "아니/없"까지 넓게 보면 "유일한 치료법이며 예약이
    # 필요 없습니다"가 독점 광고임에도 통과한다.
    "유일": re.compile(
        r"(?:전국\s*)?(?>(?:유일무이|유일)(?:한)?)"
        r"(?!\s*(?:치료법|방법)(?:은|이)?\s*(?:아니|아닙|아님))"
        r"|오직\s*이곳"
    ),
    "완치": re.compile(r"완치[율]?|완전\s*치료|완전\s*회복"),
    "100%": re.compile(r"100\s*%|백\s*퍼센트|100퍼"),
    "성공률": re.compile(r"성공률|성공\s*확률|성공\s*보장"),
    "부작용 없는": re.compile(r"부작용\s*(없|zero|제로|걱정\s*없)"),
    # 근거를 설명하는 "검증 가능한 출처"는 허용하고, 병원이 스스로 검증·입증했다고
    # 내세우는 홍보 결합만 막는다. "확인된 효과"는 기존대로 금지한다.
    "검증된": re.compile(
        r"(?:(?:저희|우리(?!나라))(?:\s*병원)?(?:가|이|에서|은|는|의)?"
        r"|병원(?:이|가|에서|은|는|의)).{0,20}(?:검증|입증)(?:된)?"
        r"|확인[된]\s*효과"
    ),
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
    "통증 없는": re.compile(
        r"통증\s*없[는이]|무통[증]?[의]?\s*(?:시술|수술|치료)"
        r"|아프지\s*않[은는]\s*(?:시술|수술|치료)"
        r"|(?:시술|수술|치료)[은는이가]?\s*아프지\s*않"
    ),
    "흉터 없는": re.compile(r"흉터\s*(없|zero|제로|걱정\s*없|남지\s*않)"),
    # 2026-10-13 대표 결정(D24) — 가격 우위·할인·이벤트·무료 유인(의료법 제56조 제2항 제13호,
    # 제27조 제3항). 생성 단계 전용 가격 검사(`content_engine.UNVERIFIED_PRICE_PATTERNS`)와 달리
    # 발행 게이트·공개 가시성·관리자 PATCH가 모두 이 패턴을 쓴다. 의학 용어와 겹치는 낱말
    # ('심혈관 이벤트가', '최저혈압', '국가 암검진은 무료')은 패턴 모양과 아래 문맥 예외로 비켜 간다.
    # 어휘 경계는 법무 확인 권장(계획 Q1).
    #
    # 정규식은 위치마다 한 번만 시도되는 모양이어야 한다 — 숫자열 앞에 경계가 없는 `\d+%\s*할인`은
    # 16,000자 숫자열에서 3초가 걸렸다(PATCH 본문에는 길이 상한이 없다). 백분율은 세 자리
    # 이하·숫자 토큰의 시작에서만 시작한다.
    # 앞의 대안이 먼저 시도된다: 할인·무료·면제와 같은 문장의 예약 권유, 혜택을 주겠다는 활용형은
    # 짧은 '무료 검사' 매치보다 먼저 긴 매치로 잡아 공적 제도 예외의 trigger에 걸리지 않게 한다.
    "할인·유인": re.compile(
        r"(?:면제|무료(?!\s*주차)|할인)[^.?!\n]{0,30}?예약(?:을|도)?\s*(?:하세요|받습니다|받아요|주세요|해\s*주세요)"
        r"|최저가(?!\s*(?:되|돼|될|된))|최저\s*(?:가격|비용|수가)|업계\s*최저|(?:가장|제일)\s*(?:저렴|싸)"
        r"|(?:타|다른)\s*(?:병원|의원|곳)\s*(?:보다|대비)[^.?!\n]{0,12}(?:저렴|(?<!비)싸|낮은\s*(?:가격|비용))"
        r"|(?<![\d.])\d{1,3}(?:\.\d{1,2})?\s*%\s*할인|할인가|할인\s*(?:이벤트|행사|혜택|쿠폰|가격|율|중)"
        r"|할인(?:을|도|이)?\s*(?:해\s*드|제공|적용|받으)"
        r"|특가|반값|파격\s*(?:가|할인)|땡처리|페이백"
        r"|(?:오픈|개원|런칭|시즌|기념|감사|할인|봄맞이|여름맞이|가을맞이|겨울맞이)\s*이벤트"
        r"|이벤트\s*(?:가격|기간|진행\s*중|혜택|참여|신청|특가)"
        r"|무료(?:로)?\s*(?:(?:상담|검사|검진|진료|시술)(?:을|를)?\s*)?(?:해\s*드|드립니다|드려요|제공|이벤트)"
        r"|무료\s*(?:상담|시술|진료|검사|검진|체험|시술권|이용권|쿠폰|이벤트)"
        r"|(?:상담|시술|진료|검사)\s*(?:비\s*|비용\s*)?(?:전액\s*)?무료"
        r"|본인\s*부담금?\s*(?:면제|할인|감면|대납|환급|0\s*원)"
        r"|사은품|경품|선착순|체험단"
    ),
}


# ── 프롬프트용 어휘 목록 ────────────────────────────────────────────────────
# FORBIDDEN_EXPRESSIONS 는 **표시용 21개**이고 실제로 글을 버리는 것은
# FORBIDDEN_PATTERNS 다. 작가에게 21개만 보여 주면 "1위", "탁월", "완치율",
# "비교 불가"처럼 패턴만 잡는 어휘를 모른 채 쓰고, 완성된 글이 통째로 폐기된다.
# 아래 목록은 패턴이 실제로 잡는 표현을 사람이 읽을 수 있게 풀어 둔 것이다.
# (정본은 여전히 FORBIDDEN_PATTERNS 다. 이 목록은 검사에 쓰이지 않는다.)
FORBIDDEN_PATTERN_VARIANTS: tuple[str, ...] = (
    "1위", "일등", "일위",
    "최상", "으뜸",
    "탁월", "가장 우수", "제일 우수",
    "가장 잘", "가장 뛰어", "제일 잘",
    "유일무이", "오직 이곳",
    "완치율", "완전 치료", "완전 회복",
    "백 퍼센트", "성공 확률", "성공 보장",
    "부작용 제로", "통증 제로",
    "확인된 효과", "효과 약속", "효과 확실",
    "비교 불가", "차별화된 노하우",
    "첨단 기술", "첨단 장비", "첨단 시술",
    "무통 시술", "아프지 않은 시술", "흉터 남지 않",
    "국내 최초", "아시아 최초", "특허 보유",
    # 할인·유인(D24, 법무 확인 권장). 표시용 FORBIDDEN_EXPRESSIONS에는 넣지 않는다 — 그 목록은
    # Essence의 필수 안전 문구(`essence_engine.MANDATORY_AVOID_MESSAGES`)에 문자열로 이어 붙어
    # 있어, 바꾸면 기존 초안·승인본이 모두 "필수 안전 규칙 없음"으로 승인·자동 검수에서 막힌다.
    "최저가", "가장 저렴", "% 할인", "할인 이벤트", "특가", "반값", "페이백",
    "개원 이벤트", "무료 상담", "무료 시술", "무료 검사", "본인부담금 면제",
    "사은품", "경품", "선착순", "체험단",
)


def forbidden_vocabulary_for_prompt() -> tuple[str, ...]:
    """작가 프롬프트에 그대로 렌더할 금지 어휘(표시용 + 패턴이 잡는 변형).

    값은 상수 조합이라 호출 시점과 무관하게 바이트 단위로 동일하다 —
    content_engine 의 정적 시스템 블록(프롬프트 캐시 접두어)에 들어가므로
    시각·UUID 같은 변동 값을 절대 섞지 말 것.
    """
    return tuple(dict.fromkeys([*FORBIDDEN_EXPRESSIONS, *FORBIDDEN_PATTERN_VARIANTS]))


# ── 문맥 예외 ───────────────────────────────────────────────────────────────
# 필터는 정본이고 경로별 허용 목록(allowlist)은 만들지 않는다. 다만 광고가 아닌
# 임상·통계·부정 문장까지 막으면 정상 글이 버려지므로, **문장 자체가 광고 주장을
# 부정하거나 수치 단위를 말하는** 좁은 문맥만 예외로 둔다. 각 예외는 광고 문형이
# 계속 막히는 양성 테스트와 임상 문형이 통과하는 음성 테스트를 함께 가진다.
@dataclass(frozen=True)
class _ContextExemption:
    """한 매치를 '광고가 아님'으로 판정하는 좁은 문맥 규칙."""

    reason: str
    trigger: re.Pattern | None = None      # 매치된 표현 자체가 이것과 완전히 일치할 때만
    preceded_by: re.Pattern | None = None  # 매치 앞 window 자 안에 이 표현이 있을 때
    followed_by: re.Pattern | None = None  # 매치 뒤 window 자 안에 이 표현이 있을 때
    # 매치 앞·뒤 window 자 안에 이 표현이 **있으면** 예외가 아니다(시술·병원 자기 주장 문맥).
    unless_preceded_by: re.Pattern | None = None
    unless_followed_by: re.Pattern | None = None
    # 매치가 든 **문장 안 어디에든** 이 표현이 있어야 예외다(양성 근거가 앞뒤 어느 쪽에 와도 된다).
    in_sentence: re.Pattern | None = None
    # 문장 안에 이 표현들이 **모두** 있으면 예외가 아니다(예: 병원 자기 지칭 + '제공').
    unless_in_sentence: tuple[re.Pattern, ...] = ()
    window: int = 30

    def applies(self, text: str, match: re.Match, sentence: str) -> bool:
        if self.trigger is not None and not self.trigger.fullmatch(match.group(0)):
            return False
        if self.in_sentence is not None and not self.in_sentence.search(sentence):
            return False
        if self.unless_in_sentence and all(
            pattern.search(sentence) for pattern in self.unless_in_sentence
        ):
            return False
        if self.preceded_by is not None:
            head = text[max(0, match.start() - self.window) : match.start()]
            if not self.preceded_by.search(head):
                return False
        if self.followed_by is not None:
            tail = text[match.end() : match.end() + self.window]
            if not self.followed_by.search(tail):
                return False
        if self.unless_preceded_by is not None:
            head = text[max(0, match.start() - self.window) : match.start()]
            if self.unless_preceded_by.search(head):
                return False
        if self.unless_followed_by is not None:
            tail = text[match.end() : match.end() + self.window]
            if self.unless_followed_by.search(tail):
                return False
        return True


# 병원이 스스로를 가리키는 말. 이 말이 가까이 있으면 문맥 예외를 주지 않는다.
_HOSPITAL_SELF = re.compile(r"저희|우리\s*(?:병원|의원)|본원|당원|병원에서|의원에서")
# 병원 자기 주장 또는 시술 결과 주장의 문맥. '첨단 장비'가 이 말 가까이 있으면 광고다.
_CLAIM_CONTEXT = re.compile(_HOSPITAL_SELF.pattern + r"|병원|의원|시술|수술|치료")
# 시술·진료 행위. '통증 없이'가 이 말 가까이 있으면 시술 결과를 보장하는 문장이다.
_PROCEDURE = re.compile(
    r"시술|수술|치료|진료|마취|주사|레이저|내시경|검사|교정|발치|임플란트|저희|본원|병원"
)


def _earlier_in_sentence(pattern: re.Pattern) -> re.Pattern:
    """매치 앞, **같은 문장 안**에 이 표현이 있는가(문장 부호·줄바꿈을 넘지 않는다)."""
    return re.compile(rf"(?:{pattern.pattern})[^.?!\n]*$")


def _later_in_sentence(pattern: re.Pattern) -> re.Pattern:
    """매치 뒤, **같은 문장 안**에 이 표현이 있는가."""
    return re.compile(rf"^[^.?!\n]*(?:{pattern.pattern})")


# 공적 제도·기관. '무료 검진'이 이 말과 함께 나오면 병원의 유인이 아니라 제도 안내다. '대상'·'연령'
# 같은 말만으로는 근거가 되지 않는다 — "신규 고객 대상 무료 검사"는 병원의 유인이다.
_PUBLIC_PROGRAM = re.compile(
    r"국가|공단|보건소|국민\s*건강\s*보험|건강\s*보험|건강\s*검진|암\s*검진|지자체|정부"
    r"|의료\s*급여|차상위|산정\s*특례|상한제|제도"
)
# 본인부담 면제·환급·0원이 제도 안내라는 양성 근거. 병원 자기 지칭이 없다는 것만으로는 풀지 않는다 —
# "본인부담금 면제! 지금 예약하세요."는 주어가 생략된 병원의 유인이다.
_COPAY_PROGRAM = re.compile(
    r"상한제|산정\s*특례|의료\s*급여|차상위|건강\s*보험|공단|국가|보건소|제도"
)
# 병원이 혜택을 주겠다는 말. 이 말이 매치 뒤 같은 문장에 있으면 제도 안내 예외를 주지 않는다.
_OFFERING = re.compile(r"해\s*드|드립니다|드려요|혜택|이벤트|특가|증정|모십니다|모셔|오세요")
# '제공'은 주체가 갈린다 — "보건소에서 무료 검사를 제공합니다"는 제도 안내이고, 병원 자기 지칭과
# 같은 문장에 있으면 병원의 유인이다(공적 근거가 없는 문장은 애초에 예외가 아니다).
_NEUTRAL_OFFER = re.compile(r"제공")
# 정기 검진 대상의 연령·주기. "만 40세 이상은 2년마다 무료 검진 대상입니다"의 양성 근거다.
_SCREENING_SCHEDULE = re.compile(
    r"(?:만\s*)?\d+\s*세|\d+\s*년\s*(?:마다|주기|에\s*한\s*번)|연령|주기"
)
# '무료 검진·검사·상담' 매치의 모양. 공적 기관이 '제공'하는 문형까지 같은 예외로 판정한다.
_PUBLIC_FREE_TRIGGER = re.compile(
    r"무료(?:로)?\s*(?:(?:검진|검사|상담)(?:을|를)?\s*)?(?:제공)?"
    r"|(?:상담|검사)\s*(?:비\s*|비용\s*)?(?:전액\s*)?무료"
)

_ALLOWED_CONTEXTS: dict[str, tuple[_ContextExemption, ...]] = {
    # "사망원인 1위", "발생률 1위" 같은 역학 통계. 역학 명사가 **바로 앞에 붙어 있을
    # 때만** 예외다 — 창을 넓게 잡으면 "발생률 1위 질환을 치료하는 국내 1위 병원"의
    # 뒤쪽 광고 주장까지 같은 문장 안에서 통과한다.
    "1등": (
        _ContextExemption(
            reason="역학 통계 문맥의 순위 인용",
            trigger=re.compile(r"1위|일위"),
            preceded_by=re.compile(
                r"(?:발생|사망|유병|발병)(?:률|율)?(?:\s*원인|\s*순위|\s*빈도)?"
                r"\s*(?:[은는이가]\s*)?$"
            ),
            window=12,
        ),
    ),
    # "최고 39도", "최고 40mg" 같은 수치 상한. 광고 최상급("최고의 진료")은 계속 차단.
    "최고": (
        # "증상이 최고조에 이릅니다", "혈당이 최고치를 기록" — 정도의 정점이지 우위 주장이 아니다.
        # "최고치료"처럼 붙여 쓴 광고는 계속 막는다.
        _ContextExemption(
            reason="정점을 뜻하는 최고조·최고치",
            trigger=re.compile(r"최고"),
            followed_by=re.compile(r"^(?:조|치(?!료))"),
            window=2,
        ),
        _ContextExemption(
            reason="수치 상한(체온·용량·연령·횟수)",
            trigger=re.compile(r"최고"),
            followed_by=re.compile(
                r"^\s*\d+(?:\.\d+)?\s*(?:도|mg|ml|회|세|시간|일|주|개월|년|kg|cm|mm|%)"
            ),
            window=12,
        ),
    ),
    # "임상시험에서 확인된 효과는 제한적입니다" — 문헌·연구의 결과를 옮기는 문장. 병원이 스스로
    # 확인했다는 주장("저희 병원 임상에서 확인된 효과")은 계속 막는다.
    "검증된": (
        _ContextExemption(
            reason="연구·문헌 결과의 인용",
            trigger=re.compile(r"확인[된]\s*효과"),
            preceded_by=re.compile(
                r"(?:임상|연구|시험|논문|문헌|진료\s*지침|학회)(?:에서|으로|를\s*통해|결과)?\s*$"
            ),
            # 병원 자기 주장은 문장 어디에 있어도 막는다("저희 병원에서 진행한 임상 연구에서 확인된 효과").
            unless_preceded_by=_earlier_in_sentence(_HOSPITAL_SELF),
            window=60,
        ),
    ),
    # "탁월한 효과를 기대하기 어렵습니다"처럼 우월 주장을 바로 부정하는 문장.
    "최우수": (
        _ContextExemption(
            reason="우월 주장을 부정하는 문장",
            trigger=re.compile(r"탁월[한]?"),
            followed_by=re.compile(r"기대하기\s*어(?:렵|려)|보장하지\s*않"),
            window=20,
        ),
    ),
    # "완치가 어렵고", "완치되지 않습니다"처럼 완치 주장을 바로 부정·한정하는 문장.
    # "완치율", "완치 가능", "완치됩니다"는 계속 차단.
    "완치": (
        # "수술 후 완전 회복까지 3개월이 걸립니다" — 회복 기간 안내. "완전 회복됩니다"·"완전 회복을
        # 보장" 같은 결과 단정은 계속 막는다.
        _ContextExemption(
            reason="완전 회복까지 걸리는 기간 안내",
            trigger=re.compile(r"완전\s*회복"),
            followed_by=re.compile(
                r"^\s*(?:까지|되기까지|하기까지)(?:는)?\s*(?:보통\s*|대개\s*|약\s*|대략\s*)?"
                r"(?:\d+(?:\s*[~～-]\s*\d+)?|수)\s*(?:일|주|개월|달|년)"
            ),
            window=24,
        ),
        _ContextExemption(
            reason="완치 주장을 바로 부정·한정",
            trigger=re.compile(r"완치"),
            followed_by=re.compile(
                r"^(?:가\s*어(?:렵|려)|되지\s*않|[을를]\s*보장할\s*수\s*없|[을를]\s*의미하지\s*않)"
            ),
            window=16,
        ),
    ),
    # "정확도가 100%는 아닙니다", "100%로 예방할 수 없습니다" 같은 한계 설명.
    "100%": (
        # "비급여 항목은 100% 본인부담입니다" — 부담 비율 안내이지 효과·안전 보장이 아니다.
        _ContextExemption(
            reason="본인부담 비율 안내",
            followed_by=re.compile(r"^\s*(?:본인\s*부담|자기\s*부담|환자\s*부담|전액\s*본인)"),
            window=10,
        ),
        _ContextExemption(
            reason="100% 주장을 바로 부정",
            followed_by=re.compile(r"^(?:[는가]\s*아(?:니|닙|님)|로\s*예방할\s*수\s*없)"),
            window=16,
        ),
    ),
    # "성공률은 개인차가 커 단정할 수 없습니다"처럼 수치를 바로 한정하는 문장.
    "성공률": (
        _ContextExemption(
            reason="성공률을 개인차·불확실성으로 한정",
            trigger=re.compile(r"성공\s*(?:률|확률)"),
            followed_by=re.compile(r"다릅니다|개인차|단정할\s*수\s*없|달라질\s*수"),
            window=20,
        ),
    ),
    # "부작용 없는 약은 없습니다" — 무부작용 주장을 존재 자체로 부정하는 문장.
    "부작용 없는": (
        _ContextExemption(
            reason="무부작용 주장을 존재 부정으로 반박",
            followed_by=re.compile(r"^는\s*(?:약|치료|시술|수술)(?:은|는|이|가)?\s*없"),
            window=20,
        ),
    ),
    # "첨단 기술이 효과를 보장하지 않습니다" 같은 부정. "최첨단"은 계속 차단.
    "최첨단": (
        # "첨단 장비로 정밀하게 촬영합니다" — 촬영·판독 과정을 설명하는 문장만 예외다(양성 근거).
        # 병원·시술을 가리키는 말이 없다는 것만으로는 풀지 않는다 — "첨단 장비를 갖추고 있습니다"는
        # 주어가 생략된 병원 자기 주장이다. "최첨단", "첨단 시술", "본원은 첨단 장비를 갖췄습니다",
        # "첨단 기술로 시술합니다"는 계속 막는다.
        _ContextExemption(
            reason="촬영·판독 과정의 장비 설명",
            trigger=re.compile(r"첨단[의]?\s*(?:기술|장비)"),
            followed_by=re.compile(r"^\s*(?:으)?로\s*(?:정밀|촬영|판독)"),
            unless_preceded_by=_CLAIM_CONTEXT,
            unless_followed_by=_CLAIM_CONTEXT,
            window=20,
        ),
        _ContextExemption(
            reason="첨단 기술 주장을 부정",
            trigger=re.compile(r"첨단[의]?\s*(?:기술|장비|시술)"),
            followed_by=re.compile(r"기대하기\s*어(?:렵|려)|보장하지\s*않"),
            window=20,
        ),
    ),
    # "국가에서 시행하는 무료 검진", "보건소에서 무료 검사", "본인부담금 면제 대상" — 공적 제도
    # 안내는 병원의 무료 유인이 아니다. 같은 문장에 병원 자기 주장(저희·본원·병원에서)이나 혜택을
    # 주겠다는 말(해 드립니다·제공·혜택·이벤트)이 있으면 예외가 아니다 — "국가 암검진 대상자가
    # 아니어도 무료 검진을 해 드립니다"는 계속 막는다.
    "할인·유인": (
        _ContextExemption(
            reason="공적 제도의 무료 검진·검사·상담 안내",
            trigger=_PUBLIC_FREE_TRIGGER,
            preceded_by=_earlier_in_sentence(_PUBLIC_PROGRAM),
            unless_preceded_by=_earlier_in_sentence(_HOSPITAL_SELF),
            unless_followed_by=_later_in_sentence(_OFFERING),
            unless_in_sentence=(_HOSPITAL_SELF, _NEUTRAL_OFFER),
            window=40,
        ),
        _ContextExemption(
            reason="공적 제도의 무료 검진·검사·상담 안내(제도가 뒤에 오는 문장)",
            trigger=_PUBLIC_FREE_TRIGGER,
            followed_by=_later_in_sentence(_PUBLIC_PROGRAM),
            unless_preceded_by=_earlier_in_sentence(_HOSPITAL_SELF),
            unless_followed_by=_later_in_sentence(_OFFERING),
            unless_in_sentence=(_HOSPITAL_SELF, _NEUTRAL_OFFER),
            window=40,
        ),
        # "만 40세 이상은 2년마다 무료 검진 대상입니다" — 기관 이름 없이 정기 검진의 연령·주기를
        # 말하는 문장. '검진 대상'과 같은 문장의 연령·주기가 함께 있어야 한다("신규 고객 대상 무료
        # 검사"는 계속 막는다).
        _ContextExemption(
            reason="연령·주기로 정한 정기 검진 대상 안내",
            trigger=re.compile(r"무료\s*검진"),
            preceded_by=_earlier_in_sentence(_SCREENING_SCHEDULE),
            followed_by=re.compile(r"^\s*대상"),
            unless_preceded_by=_earlier_in_sentence(_HOSPITAL_SELF),
            unless_followed_by=_later_in_sentence(_OFFERING),
            unless_in_sentence=(_HOSPITAL_SELF, _NEUTRAL_OFFER),
            window=40,
        ),
        # "본인부담상한제에 따라 본인부담금 환급", "차상위 계층은 본인부담금 면제 대상" — 건강보험
        # 제도 안내. 같은 문장에 제도·기관의 양성 근거가 있어야 하고, 병원이 깎아 주거나 대신 내 주겠다는
        # 말("본인부담금 면제 혜택을 드립니다")과 할인·대납은 계속 막는다.
        _ContextExemption(
            reason="건강보험 제도의 본인부담 면제·환급 안내",
            trigger=re.compile(r"본인\s*부담금?\s*(?:면제|감면|환급|0\s*원)"),
            in_sentence=_COPAY_PROGRAM,
            unless_preceded_by=_earlier_in_sentence(_HOSPITAL_SELF),
            unless_followed_by=_later_in_sentence(_OFFERING),
            unless_in_sentence=(_HOSPITAL_SELF, _NEUTRAL_OFFER),
            window=40,
        ),
    ),
    # "시술이 아프지 않을까 걱정하는 분이 많습니다" — 환자의 의문·걱정 인용.
    # "시술은 아프지 않습니다" 같은 단정은 계속 차단.
    "통증 없는": (
        # "고혈압은 초기에 통증 없이 진행합니다", "결절은 대부분 통증 없는 혹으로 발견됩니다" — 무증상
        # 질환 설명. 질환·증상 주어가 **바로 앞에** 있을 때만 예외다(양성 근거). 주어가 없는
        # "통증 없이 편안하게 받으실 수 있습니다"는 생략된 시술 결과 보장이라 계속 막고, 가까이에
        # 시술·진료·병원을 가리키는 말이 있어도 막는다.
        _ContextExemption(
            reason="무증상 질환의 경과 설명",
            trigger=re.compile(r"통증\s*없[는이]"),
            preceded_by=re.compile(
                r"(?:(?:질환|질병|증상|결절|혹|병변|종양|무증상)[은는이가의]?\s*"
                r"(?:대부분\s*|흔히\s*|초기에(?:는)?\s*)?"
                r"|초기에(?:는)?\s*)$"
            ),
            unless_preceded_by=_PROCEDURE,
            unless_followed_by=_PROCEDURE,
            window=12,
        ),
        _ContextExemption(
            reason="환자의 의문·걱정 표현",
            trigger=re.compile(r"(?:시술|수술|치료)[은는이가]?\s*아프지\s*않"),
            followed_by=re.compile(r"^을(?:까|지)"),
            window=8,
        ),
    ),
}


# ── 결과 보장 표지 — 어떤 문맥 예외보다 먼저 본다 ─────────────────────────────────
# "완전 회복까지 1주를 보장합니다", "증상은 통증 없이 사라짐을 보장합니다"처럼 예외의 양성 근거(기간·
# 질환 주어)를 갖췄어도 같은 문장이 결과를 보장하면 광고다. 예외마다 따로 막지 않고 평가기 한 곳에서
# 예외 적용 전에 판정한다. 보장을 **부정**하는 말("보장할 수 없습니다", "확실하지 않습니다")은 표지가
# 아니고, 보험 용어 '보장성·보장 범위', 비급여의 '100% 본인부담', 권고형 '반드시 상담하세요'도 아니다.
_GUARANTEE_NEGATION = (
    r"\s*(?:[을를이가은는도]\s*)?"
    r"(?:(?:하지|되지|치)(?:는|도)?|(?:할|될|드릴)\s*수(?:는|도)?"
    r"|하기|되기|(?:한|된|하는|되는)\s*(?:것은|건))?"
    r"\s*(?:않|없|못|어렵|어려|아니|아닙|아님)"
)
_OUTCOME_GUARANTEE = re.compile(
    r"(?:(?<!보험)(?<!보험\s)보장(?!\s*(?:성|범위|항목|내용|률|율|비율|한도|대상))"
    r"|확실|무조건|틀림없|장담|약속"
    r"|100\s*%(?!\s*(?:본인|자기|환자|전액)\s*(?:부담|본인))|백\s*퍼센트"
    r"|반드시(?![^.?!\n]{0,20}?(?:상담|확인|문의|방문|받으세요|받아야|하세요|해야|필요|주의)))"
    rf"(?!{_GUARANTEE_NEGATION})"
)
# 문장 경계: 마침표·물음표·느낌표(소수점 '1.5'는 경계가 아니다)와 줄바꿈.
_SENTENCE_BOUNDARY = re.compile(r"[.?!…。](?!\d)|\n")
# 문장 판정은 매치 앞뒤 이 글자 수 안으로 자른다 — 마침표 없는 긴 입력에서 매치마다 문장 전체를
# 다시 훑으면 이차 시간이 된다(실제 문장은 이보다 짧다).
_SENTENCE_CLIP = 200


class _SentenceIndex:
    """한 텍스트의 문장 경계와 보장 표지 위치. 처음 필요할 때 한 번만 만든다."""

    def __init__(self, text: str):
        self.text = text
        self._boundary_starts: list[int] | None = None
        self._boundary_ends: list[int] = []
        self._guarantees: list[tuple[int, int]] = []
        self._guarantee_starts: list[int] = []

    def _build(self) -> None:
        if self._boundary_starts is not None:
            return
        boundaries = [(m.start(), m.end()) for m in _SENTENCE_BOUNDARY.finditer(self.text)]
        self._boundary_starts = [start for start, _ in boundaries]
        self._boundary_ends = [end for _, end in boundaries]
        self._guarantees = [(m.start(), m.end()) for m in _OUTCOME_GUARANTEE.finditer(self.text)]
        self._guarantee_starts = [start for start, _ in self._guarantees]

    def sentence_span(self, match: re.Match) -> tuple[int, int]:
        """매치가 든 문장의 [시작, 끝)을 앞뒤 `_SENTENCE_CLIP`자 안으로 자른 구간."""
        self._build()
        index = bisect_right(self._boundary_ends, match.start()) - 1
        start = self._boundary_ends[index] if index >= 0 else 0
        index = bisect_left(self._boundary_starts, match.end())
        end = self._boundary_starts[index] if index < len(self._boundary_starts) else len(self.text)
        return max(start, match.start() - _SENTENCE_CLIP), min(end, match.end() + _SENTENCE_CLIP)

    def guarantees_outcome(self, match: re.Match, span: tuple[int, int]) -> bool:
        """매치 자신을 뺀 같은 문장 안에 결과 보장 표지가 있는가."""
        self._build()
        index = bisect_left(self._guarantee_starts, span[0])
        while index < len(self._guarantees):
            start, end = self._guarantees[index]
            if start >= span[1]:
                return False
            if end <= match.start() or start >= match.end():
                return True
            index += 1
        return False


def _blocking_match_exists(
    label: str, pattern: re.Pattern, text: str, sentences: _SentenceIndex
) -> bool:
    """이 라벨의 매치 중 문맥 예외가 아닌 것이 하나라도 있는가.

    예외는 같은 문장이 결과를 보장하지 않을 때만 적용된다(`_OUTCOME_GUARANTEE`).
    """
    exemptions = _ALLOWED_CONTEXTS.get(label, ())
    for match in pattern.finditer(text):
        if not exemptions:
            return True
        span = sentences.sentence_span(match)
        sentence = text[span[0] : span[1]]
        if not any(exemption.applies(text, match, sentence) for exemption in exemptions):
            return True
        if sentences.guarantees_outcome(match, span):
            return True
    return False


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
    sentences = _SentenceIndex(normalized)
    found = []
    for label, pattern in FORBIDDEN_PATTERNS.items():
        if _blocking_match_exists(label, pattern, normalized, sentences):
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
