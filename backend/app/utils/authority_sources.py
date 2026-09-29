"""의료 콘텐츠 인용용 권위 출처 화이트리스트.

GEO 논문(Princeton·Georgia Tech 2024)·5W Citation Source Audit Q1 2026·
SE Ranking YMYL Health Study(2025) 등에서 AI 답변(ChatGPT/Gemini/Perplexity)
인용 가중치가 입증된 도메인만 추립니다. 의료광고법(제56조) 광고 유인성을
유발하지 않는 비영리·공공·학술 출처만 포함합니다.

용도:
- content_engine.py 프롬프트에 주입해 인용 후보를 제한.
- _normalize_references 단계에서 white-list domain 외 항목 검출(선택).
"""

import re
from urllib.parse import parse_qsl, unquote, urlparse

KR_PUBLIC_SOURCES: list[dict[str, str]] = [
    {"name": "질병관리청 국가건강정보포털", "domain": "health.kdca.go.kr"},
    {"name": "질병관리청 KDCA", "domain": "kdca.go.kr"},
    {"name": "국가암정보센터", "domain": "cancer.go.kr"},
    {"name": "건강보험심사평가원 HIRA", "domain": "hira.or.kr"},
    {"name": "보건복지부", "domain": "mohw.go.kr"},
    {"name": "식품의약품안전처 MFDS", "domain": "mfds.go.kr"},
    {"name": "의료기관 평가인증원 KOIHA", "domain": "koiha.or.kr"},
    {"name": "국민건강보험공단 NHIS", "domain": "nhis.or.kr"},
]

KR_ACADEMIC_SOURCES: list[dict[str, str]] = [
    {"name": "대한의학회 KAMS", "domain": "kams.or.kr"},
    {"name": "대한의사협회", "domain": "kma.org"},
    {"name": "KMbase 의과학연구정보센터", "domain": "kmbase.medric.or.kr"},
    {"name": "KoreaMed", "domain": "koreamed.org"},
    {"name": "한국학술지인용색인 KCI", "domain": "kci.go.kr"},
    # 진료지침/질환백과로 콘텐츠 프롬프트가 직접 인용을 지시하는 학회·병원 권위 출처.
    # 누락 시 모델이 실제로 인용해도 _normalize_references가 조용히 떨궈 발행이 막힌다.
    {"name": "대한대장항문학회", "domain": "colon.or.kr"},
    {"name": "대한외과학회", "domain": "surgery.or.kr"},
    {"name": "서울아산병원 질환백과", "domain": "amc.seoul.kr"},
    # 주요 진료과 학회 — 도메인 확인됨(WebSearch 2026-07 기준 공식 홈페이지).
    {"name": "대한정형외과학회", "domain": "koa.or.kr"},
    {"name": "대한피부과학회", "domain": "derma.or.kr"},
    {"name": "대한산부인과학회", "domain": "ksog.org"},
    {"name": "대한비뇨의학회", "domain": "urology.or.kr"},
    {"name": "대한치과의사협회", "domain": "kda.or.kr"},
]

US_GLOBAL_SOURCES: list[dict[str, str]] = [
    {"name": "PubMed", "domain": "pubmed.ncbi.nlm.nih.gov"},
    {"name": "NIH 국립보건원", "domain": "nih.gov"},
    {"name": "CDC 미국 질병통제예방센터", "domain": "cdc.gov"},
    {"name": "MedlinePlus", "domain": "medlineplus.gov"},
    {"name": "Mayo Clinic", "domain": "mayoclinic.org"},
    {"name": "Cleveland Clinic", "domain": "my.clevelandclinic.org"},
    {"name": "Healthline", "domain": "healthline.com"},
    {"name": "WebMD", "domain": "webmd.com"},
    {"name": "WHO 세계보건기구", "domain": "who.int"},
    {"name": "Cochrane Library", "domain": "cochranelibrary.com"},
]

ENCYCLOPEDIA_SOURCES: list[dict[str, str]] = [
    {"name": "한국어 위키백과", "domain": "ko.wikipedia.org"},
    {"name": "Wikipedia", "domain": "en.wikipedia.org"},
]

WHITELIST_DOMAINS: frozenset[str] = frozenset(
    item["domain"]
    for group in (KR_PUBLIC_SOURCES, KR_ACADEMIC_SOURCES, US_GLOBAL_SOURCES, ENCYCLOPEDIA_SOURCES)
    for item in group
)

_TRAUMA_EMERGENCY_KEYWORDS = (
    "경증응급외상",
    "경증응급",
    "응급외상",
    "외상치료",
    "외상진료",
    "외상처치",
)

_ORTHOPEDIC_FAQ_KEYWORDS = (
    "정형외과",
    "병원선택",
    "병원선택기준",
    "통증종류",
    "통증종류별",
)
# 위 묶음은 예전에 병원 고르기 FAQ를 정형외과 문서로 보내던 경로다. 질환·시술 이름이 아니므로
# 치유 선택·수기 문서 주제 대조·병원 선택 글의 '의료 주제' 판정 어디서도 세지 않는다
# (`keyword_names_provider`). 무릎관절염 5969는 이 키워드뿐이라 카탈로그 제목 대조로만 통과한다.
PROVIDER_ROUTING_KEYWORDS: frozenset[str] = frozenset(_ORTHOPEDIC_FAQ_KEYWORDS)

# 고르는 대상(병원·전문의·진료과 이름의 끝말). '진료과목'은 '진료과'를 담지만 '진료과목선택'을
# 잡으려고 따로 둔다. 진료과 이름은 끝말(내과·외과·의학과…)로 묶는다 — '신경외과'·'소화기내과'·
# '마취통증의학과'. 병원 선택 글 판정(`reference_requirement`)과 수기 목록 키워드 채점이 같이 쓴다.
PROVIDER_NOUNS: tuple[str, ...] = (
    "병원",
    "의원",
    "전문의",
    "진료과목",
    "진료과",
    "내과",
    "외과",
    "의학과",
    "청소년과",
    "부인과",
    "피부과",
    "이비인후과",
    "안과",
    "치과",
)
_PROVIDER_ROUTING_TEXTS: frozenset[str] = frozenset(
    re.sub(r"[\W_]+", "", keyword.lower()) for keyword in PROVIDER_ROUTING_KEYWORDS
)


def keyword_names_provider(keyword: object) -> bool:
    """수기 목록 키워드가 질환·시술이 아니라 고르는 대상·고르기 경로를 가리키는가.

    '정형외과'·'심장내과'·'순환기내과'(진료과 이름)와 병원 고르기 FAQ 경로 키워드
    (`PROVIDER_ROUTING_KEYWORDS`: 병원선택·통증종류 …)는 의료 주제가 아니다. 이 키워드만 겹친
    글('노원구 마취통증의학과 병원 추천', '정형외과 병원 고를 때')에 요통·디스크 문서를 붙이면
    가짜 근거다 — 치유 선택·수기 문서 주제 판정·의료 주제 판정 모두 이 키워드를 세지 않는다.
    """

    text = re.sub(r"[\W_]+", "", str(keyword or "").lower())
    return text in _PROVIDER_ROUTING_TEXTS or any(noun in text for noun in PROVIDER_NOUNS)

# '간질환' 단독은 공백·구두점을 지운 비교에서 "회복 기간 — 질환과"(기간질환)에도 붙는다
# (6b70fe41 고압산소 글이 간염 문서를 받는 것을 재생에서 확인) — 뒤에 오는 말까지 묶는다.
_LIVER_DISEASE_KEYWORDS = (
    "간질환환자",
    "간질환치료",
    "간질환진료",
    "간질환전문",
    "간질환검사",
    "만성간질환",
)

# 브라우징 없이 생성하는 모델에게 URL을 추측시키면 존재하는 다른 질환 문서나 기관
# 홈페이지가 인용되는 문제가 생긴다. 아래 목록은 사람이 실제 제목과 URL을 확인한
# 특정 문서만 담는 작은 신뢰 카탈로그다. 키워드가 맞는 문서가 있을 때는 모델이 만든
# URL보다 이 목록을 우선한다.
CURATED_MEDICAL_SOURCE_PAGES: tuple[dict[str, object], ...] = (
    {
        "keywords": (
            "고혈압",
            "혈압관리",
            "수축기혈압",
            "이완기혈압",
            "심장내과",
            "순환기내과",
        ),
        "title": "질병관리청 국가건강정보포털 — 고혈압",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6765",
    },
    {
        "keywords": (
            "허혈성심장질환",
            "허혈성심질환",
            "협심증",
            "관상동맥질환",
            "심근경색",
            "심장내과",
            "순환기내과",
        ),
        "title": "질병관리청 국가건강정보포털 — 협심증",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6566",
    },
    {
        "keywords": (
            "뇌졸중",
            "뇌경색",
            "뇌출혈",
            "뇌혈관질환",
            "심뇌혈관질환",
            "심장내과",
            "순환기내과",
        ),
        "title": "질병관리청 국가건강정보포털 — 뇌졸중",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5495",
    },
    {
        "keywords": (
            *_TRAUMA_EMERGENCY_KEYWORDS,
            "골절",
            "뼈가부러",
        ),
        "title": "질병관리청 국가건강정보포털 — 골절",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5463",
    },
    {
        "keywords": (
            *_TRAUMA_EMERGENCY_KEYWORDS,
            "상처봉합",
            "피부열상",
            "열상봉합",
            "찢어진상처",
        ),
        "title": "질병관리청 국가건강정보포털 — 열상",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5679",
    },
    {
        "keywords": (
            *_TRAUMA_EMERGENCY_KEYWORDS,
            "상처봉합",
            "상처관리",
            "찰과상",
            "타박상",
            "자상처치",
            "찔린상처",
        ),
        "title": "질병관리청 국가건강정보포털 — 상처관리와 흉터예방",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5696",
    },
    {
        # '통증'·'관절'은 어깨·손목·무릎 글에도 요통 문서를 붙였다(2026-09-29 점검 후속) —
        # 허리·척추·도수치료를 말하는 글에만 붙는다. 선택은 공백·구두점을 지운 부분 문자열
        # 비교라 단독 '허리'·'도수'는 허리둘레(대사증후군)·빈도수·알코올/안경 도수 글에도
        # 걸린다 — '도수치료'·'허리디스크'·'허리다리'로 묶는다(추간판탈출증 항목도 같다).
        "keywords": (
            *_ORTHOPEDIC_FAQ_KEYWORDS,
            "척추",
            "요통",
            "허리통증",
            "도수치료",
            "허리디스크",
            "허리다리",
        ),
        "title": "질병관리청 국가건강정보포털 — 요통",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=3796",
    },
    {
        "keywords": _ORTHOPEDIC_FAQ_KEYWORDS,
        "title": "질병관리청 국가건강정보포털 — 무릎관절염, 올바로 운동하기",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5969",
    },
    {
        "keywords": (
            *_ORTHOPEDIC_FAQ_KEYWORDS,
            "척추",
            "추간판",
            "디스크",
            "도수치료",
            "허리디스크",
            "허리다리",
        ),
        "title": "질병관리청 국가건강정보포털 — 추간판탈출증(디스크)",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=3348",
    },
    {
        "keywords": ("체외충격파", "충격파치료", "ESWT"),
        "title": (
            "Extracorporeal shock wave therapy is effective in treating chronic "
            "plantar fasciitis: A meta-analysis of RCTs"
        ),
        "url": "https://pubmed.ncbi.nlm.nih.gov/28403111/",
    },
    {
        "keywords": ("체외충격파", "충격파치료", "ESWT"),
        "title": (
            "The evolving use of extracorporeal shock wave therapy in managing "
            "musculoskeletal and neurological diagnoses"
        ),
        "url": (
            "https://www.mayoclinic.org/medical-professionals/"
            "physical-medicine-rehabilitation/news/"
            "the-evolving-use-of-extracorporeal-shock-wave-therapy-in-managing-"
            "musculoskeletal-and-neurological-diagnoses/mac-20527246"
        ),
    },
    {
        "keywords": ("소아발열", "아이발열", "어린이발열"),
        "title": "질병관리청 국가건강정보포털 — 불명열(발열의 평가와 치료)",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5285",
    },
    {
        "keywords": ("탈수", "수분부족", "수분보충"),
        "title": "질병관리청 국가건강정보포털 — 탈수",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6551",
    },
    {
        "keywords": ("구토", "오심", "메스꺼움"),
        "title": "질병관리청 국가건강정보포털 — 오심과 구토",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5804",
    },
    {
        "keywords": ("유방초음파", "유방촬영", "유방암검진"),
        "title": "국립암센터 — 국가암검진 검진주기 및 검진방법",
        "url": "https://edu.cancer.go.kr/lay1/S1T553C555/contents.do",
    },
    {
        "keywords": ("건강검진", "일반건강검진", "국가건강검진"),
        "title": "국민건강보험공단 — 건강검진 실시기준",
        "url": "https://www.nhis.or.kr/lm/lmxsrv/law/lawFullContent.do?MODE=threeView&SEQ=80&SEQ_HISTORY=592019",
    },
    {
        "keywords": ("치열", "항문열상"),
        "title": "서울아산병원 질환백과 — 치열",
        "url": "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId=31773",
    },
    {
        "keywords": ("치루", "항문농양", "항문직장농양"),
        "title": "질병관리청 국가건강정보포털 — 항문직장농양과 치루",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=3136",
    },
    {
        "keywords": ("치루", "항문농양", "항문직장농양"),
        "title": "서울아산병원 질환백과 — 치루",
        "url": "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId=31775",
    },
    {
        "keywords": ("치핵", "치질", "치핵수술", "치질수술"),
        "title": "질병관리청 국가건강정보포털 — 치핵",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5818",
    },
    {
        "keywords": ("치핵", "치질", "치핵수술", "치질수술"),
        "title": "서울아산병원 질환백과 — 치핵",
        "url": "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId=31772",
    },
    {
        # '내시경'만으로는 위내시경 글에도 대장내시경 문서가 붙는다.
        "keywords": ("대장내시경", "장정결"),
        "title": "질병관리청 국가건강정보포털 — 대장내시경검사",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5254",
    },
    {
        "keywords": ("대장용종", "대장폴립", "용종절제", "용종"),
        "title": "질병관리청 국가건강정보포털 — 대장용종",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6531",
    },
    {
        "keywords": ("대장암", "대장암검진", "암검진"),
        "title": "국가암정보센터 — 국가암검진사업",
        "url": "https://www.cancer.go.kr/lay1/S1T261C262/contents.do",
    },
    {
        "keywords": ("변비", "배변장애", "딱딱한변"),
        "title": "질병관리청 국가건강정보포털 — 변비",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5827",
    },
    {
        "keywords": ("혈변", "흑변", "대변출혈", "피가묻"),
        "title": "질병관리청 국가건강정보포털 — 혈변 및 흑변(성인)",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5434",
    },
    # ── 2026-09-29 김실장 2차 점검에서 실제 GET(200·같은 주소·실제 문서 제목·주제어 등장)으로
    # 확인하고 공개 글 교정에 쓴 문서(/workspace/ref-audit2-20260929/verify). 키워드는 실제
    # 문서 제목에서만 뽑았고 '통증'·'검사' 같은 일반어는 넣지 않는다. 오프라인 fixture로 같은
    # 검증을 통과함을 고정한다(tests/fixtures/reference_audit_20260929/catalog_seed.json).
    {
        "keywords": ("당뇨병", "당뇨", "혈당"),
        "title": "질병관리청 국가건강정보포털 — 당뇨병",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5305",
    },
    {
        "keywords": ("복부초음파",),
        "title": "질병관리청 국가건강정보포털 — 복부초음파검사",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=1061",
    },
    {
        "keywords": ("족저근막염", "족저근막"),
        "title": "질병관리청 국가건강정보포털 — 족저근막염",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5975",
    },
    {
        "keywords": ("오십견", "동결견", "유착관절낭염", "유착성관절낭염"),
        "title": "질병관리청 국가건강정보포털 — 오십견(동결견, 유착관절낭염)",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=1567",
    },
    {
        "keywords": ("수근굴", "수근관", "손목터널"),
        "title": "질병관리청 국가건강정보포털 — 수근굴(수근관) 증후군",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6292",
    },
    {
        "keywords": ("골관절염", "퇴행성관절염", "무릎관절염"),
        "title": "질병관리청 국가건강정보포털 — 골관절염",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=1988",
    },
    {
        "keywords": ("이상지질혈증", "고지혈증", "콜레스테롤"),
        "title": "질병관리청 국가건강정보포털 — 이상지질혈증",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6715",
    },
    {
        "keywords": ("지질검사", "혈중지질"),
        "title": "질병관리청 국가건강정보포털 — 지질 검사",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6709",
    },
    {
        "keywords": ("위내시경",),
        "title": "질병관리청 국가건강정보포털 — 위내시경",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5258",
    },
    {
        "keywords": ("지방간",),
        "title": "질병관리청 국가건강정보포털 — 대사이상지방간질환",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6673",
    },
    {
        "keywords": ("갑상선초음파", "갑상샘초음파", "갑상선결절", "갑상샘결절"),
        "title": "질병관리청 국가건강정보포털 — 갑상선 검사(초음파)",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=2390",
    },
    {
        "keywords": ("간기능", "간수치"),
        "title": "질병관리청 국가건강정보포털 — 간기능검사",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5444",
    },
    {
        "keywords": ("부정맥",),
        "title": "질병관리청 국가건강정보포털 — 부정맥",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=1102",
    },
    {
        "keywords": ("경동맥초음파", "경동맥도플러", "경동맥협착"),
        "title": "MedlinePlus — Carotid duplex (경동맥 초음파)",
        "url": "https://medlineplus.gov/ency/article/003774.htm",
    },
    {
        "keywords": ("고압산소",),
        "title": "MedlinePlus — Hyperbaric oxygen therapy (고압산소치료)",
        "url": "https://medlineplus.gov/ency/article/002375.htm",
    },
    # ── 2026-09-29 보류 재생(PR #177 리뷰) 후속: 보류를 만든 주제군의 문서. 실제 GET(200·같은 주소·
    # 본문 1,500자 이상·문서 주제 확인)과 오프라인 검증기 통과를 확인했다
    # (/workspace/ref-url-guard/r2/vetting). 영문 문서는 제목으로 주제를 판정할 수 없어
    # (undeterminable) 목록에 있어야만 남는다 — 한국어 키워드로 글 주제에 묶는다. 오프라인
    # fixture: tests/fixtures/reference_audit_20260929/catalog_seed_r3.json.
    {
        "keywords": ("예방접종", "백신접종", "예방주사"),
        "title": "MedlinePlus — Vaccines (immunizations) (예방접종)",
        "url": "https://medlineplus.gov/ency/article/002024.htm",
    },
    {
        "keywords": ("인플루엔자", "독감"),
        "title": "질병관리청 국가건강정보포털 — 인플루엔자",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5232",
    },
    {
        "keywords": ("대상포진", "수두"),
        "title": "질병관리청 국가건강정보포털 — 수두와 대상포진",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6679",
    },
    {
        "keywords": ("자궁경부암백신", "자궁경부암예방접종", "HPV백신", "사람유두종바이러스"),
        "title": "질병관리청 국가건강정보포털 — 자궁경부암 백신",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=3987",
    },
    {
        "keywords": (*_LIVER_DISEASE_KEYWORDS, "바이러스성간염", "간염바이러스"),
        "title": "질병관리청 국가건강정보포털 — 바이러스성 간염",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6553",
    },
    {
        "keywords": ("B형간염",),
        "title": "질병관리청 국가건강정보포털 — B형간염",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6672",
    },
    {
        "keywords": ("간경변", "간경화", *_LIVER_DISEASE_KEYWORDS),
        "title": "질병관리청 국가건강정보포털 — 간경변증",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6560",
    },
    {
        "keywords": ("간경변", "간경화"),
        "title": "서울아산병원 질환백과 — 간경화",
        "url": "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId=30480",
    },
    {
        "keywords": (*_LIVER_DISEASE_KEYWORDS, "급성간염", "만성간염"),
        "title": "서울아산병원 질환백과 — 간염",
        "url": "https://www.amc.seoul.kr/asan/healthinfo/disease/diseaseDetail.do?contentId=31687",
    },
    {
        "keywords": ("건강검진", "일반건강검진", "국가건강검진"),
        "title": "국민건강보험공단 — 일반건강검진 실시안내",
        "url": "https://www.nhis.or.kr/nhis/healthin/wbhaca04500m01.do",
    },
    {
        "keywords": ("건강검진", "일반건강검진", "국가건강검진", "검진결과"),
        "title": "질병관리청 국가건강정보포털 — 알아두면 도움이 되는 건강검진",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/ntcnInfo/healthSourc/thtimtCntnts/thtimtCntntsView.do?thtimt_cntnts_sn=7",
    },
    {
        "keywords": ("건강검진", "국가건강검진", "암검진"),
        "title": "질병관리청 국가건강정보포털 — 건강검진(암 검진)",
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5296",
    },
    {
        "keywords": ("PRP", "자가혈", "혈소판풍부혈장"),
        "title": "Cleveland Clinic — Platelet-Rich Plasma (PRP) Injection (자가혈 혈소판풍부혈장 주사)",
        "url": "https://my.clevelandclinic.org/health/treatments/platelet-rich-plasma-prp-injection",
    },
    {
        "keywords": ("수액", "정맥주사", "링거"),
        "title": "Cleveland Clinic — IV Fluids (수액·정맥 수액 요법)",
        "url": "https://my.clevelandclinic.org/health/treatments/21635-iv-fluids",
    },
    # 영문 MedlinePlus 문서는 목록 밖이면 undeterminable로 빠졌다(cbdafc4e CT, 42ef2b13 골밀도).
    # 'CT' 단독은 소문자 부분 문자열 비교에서 injection·doctor 같은 영어 단어에 걸린다.
    {
        "keywords": ("CT검사", "CT촬영", "컴퓨터단층촬영", "전산화단층촬영"),
        "title": "MedlinePlus — CT Scans (CT·컴퓨터단층촬영)",
        "url": "https://medlineplus.gov/ctscans.html",
    },
    {
        "keywords": ("골다공증", "골밀도"),
        "title": "MedlinePlus — Osteoporosis (골다공증·골밀도)",
        "url": "https://medlineplus.gov/osteoporosis.html",
    },
)

# Schema.org / 운영 통계용 카테고리 식별자. 콘텐츠 references[].source_type 값으로 사용.
SOURCE_TYPE_GOV_KR = "GOV_KR"           # 한국 정부·공공
SOURCE_TYPE_ACADEMIC_KR = "ACADEMIC_KR" # 한국 학회·학술
SOURCE_TYPE_GOV_GLOBAL = "GOV_GLOBAL"   # 국제 정부·기관 (NIH/CDC/WHO 등)
SOURCE_TYPE_CLINIC = "CLINIC_REFERENCE" # Mayo/Cleveland/Healthline 등 임상 정보
SOURCE_TYPE_ENCYCLOPEDIA = "ENCYCLOPEDIA"

_DOMAIN_TO_SOURCE_TYPE: dict[str, str] = {}
for item in KR_PUBLIC_SOURCES:
    _DOMAIN_TO_SOURCE_TYPE[item["domain"]] = SOURCE_TYPE_GOV_KR
for item in KR_ACADEMIC_SOURCES:
    _DOMAIN_TO_SOURCE_TYPE[item["domain"]] = SOURCE_TYPE_ACADEMIC_KR
for item in US_GLOBAL_SOURCES:
    domain = item["domain"]
    if domain in {"nih.gov", "cdc.gov", "medlineplus.gov", "who.int", "pubmed.ncbi.nlm.nih.gov"}:
        _DOMAIN_TO_SOURCE_TYPE[domain] = SOURCE_TYPE_GOV_GLOBAL
    else:
        _DOMAIN_TO_SOURCE_TYPE[domain] = SOURCE_TYPE_CLINIC
for item in ENCYCLOPEDIA_SOURCES:
    _DOMAIN_TO_SOURCE_TYPE[item["domain"]] = SOURCE_TYPE_ENCYCLOPEDIA


def _extract_hostname(url: str) -> str | None:
    """URL에서 hostname만 안전하게 추출.

    문자열 포함 검사(f".{domain}" in lowered)는 kdca.go.kr.evil.com 같은
    스푸핑 도메인을 kdca.go.kr로 오매칭한다. urlparse로 실제 hostname을 뽑아
    호스트명 자체를 비교해야 한다.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        # 깨진 포트(`host:bad`)는 hostname만 보면 화이트리스트를 통과하고 뒤에서 `.port`가
        # ValueError를 던진다 — 인용할 수 없는 주소로 거절한다.
        _ = parsed.port
    except ValueError:
        return None
    return hostname.lower() if hostname else None


def _matches_domain(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def infer_source_type(url: str) -> str | None:
    """URL이 화이트리스트의 어떤 카테고리에 속하는지 반환. 매칭 실패 시 None."""
    hostname = _extract_hostname(url)
    if not hostname:
        return None
    for domain, source_type in _DOMAIN_TO_SOURCE_TYPE.items():
        if _matches_domain(hostname, domain):
            return source_type
    return None


def is_whitelisted_url(url: str) -> bool:
    """URL이 권위 출처 화이트리스트에 속하는지 검사. 서브도메인 매칭 포함.

    hostname == domain 또는 hostname이 ".domain"으로 끝나는 엄격 비교만 허용한다.
    (스푸핑 도메인 회귀 방지 — kdca.go.kr.evil.com은 hostname 자체가 다르므로 탈락)
    """
    hostname = _extract_hostname(url)
    if not hostname:
        return False
    for domain in WHITELIST_DOMAINS:
        if _matches_domain(hostname, domain):
            return True
    return False


_DOMAIN_TO_INSTITUTION_NAME: dict[str, str] = {
    item["domain"]: item["name"]
    for group in (KR_PUBLIC_SOURCES, KR_ACADEMIC_SOURCES, US_GLOBAL_SOURCES, ENCYCLOPEDIA_SOURCES)
    for item in group
}


# 사람이 제목·URL을 직접 확인한 문서들. 주제 적합성 채점은 이 URL을 건너뛴다
# (구성상 신뢰되며, 빈 references를 치유하는 경로도 여기서 값을 가져온다).
CURATED_SOURCE_URLS: frozenset[str] = frozenset(
    str(source["url"]) for source in CURATED_MEDICAL_SOURCE_PAGES
)

# 사람이 실제 GET으로 확인해 근거로 쓸 수 없다고 판정한 주소(2026-09-29 김실장 2차 점검,
# /workspace/ref-fix-20260929/REPORT_FULL_2.md). 수기 목록에도, 모델 참고자료로도 쓰지 않는다 —
# 사이트가 언젠가 리다이렉트를 멈추거나 제목만 채워 돌려줘도 막힌다. 비교는 수기 목록과 같은
# 문서 동일성 판정으로 한다(`_matching_documents` — 별칭도 같은 제외 주소다).
REFERENCE_URL_EXCLUSIONS: tuple[dict[str, str], ...] = (
    {
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6263",
        "topic": "소화불량",
        "reason": (
            "200이지만 /healthinfo/ 메인으로 리다이렉트되는 빈 템플릿(문서명 칸이 빈 제목) — "
            "검색 색인에는 '소화불량'으로 있으나 실제 문서가 없다"
        ),
    },
    {
        "url": "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=2351",
        "topic": "당뇨병 합병증",
        "reason": (
            "200이지만 /healthinfo/ 메인으로 리다이렉트되는 빈 템플릿(문서명 칸이 빈 제목) — "
            "검색 색인에는 '당뇨병 합병증'으로 있으나 실제 문서가 없다"
        ),
    },
    {
        "url": "https://www.cancer.go.kr/lay1/S1T211C213/contents.do",
        "topic": "암의 종류 > 위암",
        "reason": "국가암정보센터 허브 페이지 — 제목(breadcrumb)만 있고 본문이 비어 있다",
    },
    {
        "url": "https://www.cancer.go.kr/lay1/S1T211C214/contents.do",
        "topic": "암의 종류 > 대장암",
        "reason": "국가암정보센터 허브 페이지 — 제목(breadcrumb)만 있고 본문이 비어 있다",
    },
    {
        "url": "https://www.cancer.go.kr/lay1/S1T274C286/contents.do",
        "topic": "치료 > 수술",
        "reason": "국가암정보센터 허브 페이지 — 제목(breadcrumb)만 있고 본문이 비어 있다",
    },
    {
        "url": "https://cancer.go.kr/lay1/program/S1T211C223/cancer/view.do?cancer_seq=3797",
        "topic": "대장암",
        "reason": (
            "200·같은 주소지만 제목이 breadcrumb뿐이고 본문(요약설명)이 얇다 — 2차 점검 verify "
            "ok=false(본문 1,110자, '대장암' 7회)"
        ),
    },
)


# 수기 목록·제외 목록 URL에 실제로 쓰인 질의 이름 가운데 문서를 가르는 것. 나머지(`MODE` 같은
# 보기 방식, 모르는 `utm_source`·`from` …)는 같은 문서를 가리키므로 무시한다.
CURATED_DOCUMENT_ID_PARAMS: frozenset[str] = frozenset(
    {"cntnts_sn", "contentId", "thtimt_cntnts_sn", "SEQ", "SEQ_HISTORY", "cancer_seq"}
)
_DEFAULT_PORTS: frozenset[int] = frozenset({80, 443})
# 값에서 숫자만 모아 읽는 id 이름. KDCA `cntnts_sn`은 실제 서버가 `a3796`·`-3796`·`37a96`·
# `3796%26x%3D`를 모두 요통(3796) 문서로, `3796-1`·`3796%26x%3D1`은 없는 문서(37961)로 돌려준다
# (PR #177 5차 리뷰 공개 GET). 나머지 이름은 그런 관측이 없어 앞 정수 규칙을 그대로 쓴다.
_DIGITS_ONLY_ID_PARAMS: frozenset[str] = frozenset({"cntnts_sn"})
# 숫자는 ASCII `0-9`만이다. `\d`는 전각 `３`·아랍 숫자까지 받는데 서버가 그 값을 같은 문서로
# 읽는다는 관측이 없다 — 전각 id 주소는 목록 밖 주소로 실제 GET 판정을 받는다.
_LEADING_INTEGER = re.compile(r"\s*\+?([0-9]+)")
_NON_DIGITS = re.compile(r"[^0-9]+")
_DOCUMENT_ID_MAX_DIGITS = 18


def _document_id_value(value: str, *, name: str = "") -> int | None:
    """서버처럼 id 값을 정수로 읽는다 — `3796abc`·`3796+`·`03796`은 모두 3796.

    `cntnts_sn`(`_DIGITS_ONLY_ID_PARAMS`)은 값의 숫자만 모은다(`a3796`·`37a96`도 3796). 다른
    이름은 앞 정수다. 숫자는 ASCII `0-9`만 읽는다 — 전각 `３７９６`은 숫자가 없는 값이고, 섞인
    `3７96`은 `cntnts_sn`이면 396, 앞 정수 이름이면 3이다. 읽을 숫자가 없는 값(`abc`·빈 값·전각만)은
    어떤 문서 id와도 맞지 않는다.
    """

    if name in _DIGITS_ONLY_ID_PARAMS:
        found = _NON_DIGITS.sub("", value)
    else:
        match = _LEADING_INTEGER.match(value)
        found = match.group(1) if match is not None else ""
    if not found:
        return None
    digits = found.lstrip("0")
    if len(digits) > _DOCUMENT_ID_MAX_DIGITS:
        return None  # 목록 문서 id가 될 수 없는 길이(정수 변환 한도 전에 끊는다)
    return int(digits or "0")


def _document_location(url: object) -> tuple[str, list[tuple[str, str]]] | None:
    """(문서 위치, 질의 쌍) — 위치는 scheme·앞의 `www.`·호스트 대소문자·기본 포트(80·443)·
    퍼센트 인코딩·겹친 `/`·끝 슬래시·fragment·`;params`를 지운 호스트+경로다. 경로 대소문자는
    다른 문서(대개 404)라 그대로 둔다."""

    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"https://{text}"
    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError:
        return None  # 깨진 포트 — 화이트리스트 밖이라 인용되지 않는다
    host = (parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    if port and port not in _DEFAULT_PORTS:
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", unquote(parsed.path)).rstrip("/")
    return f"{host}{path}", parse_qsl(parsed.query, keep_blank_values=True)


def _document_matcher(url: str) -> tuple[str, tuple[tuple[str, int], ...]]:
    """목록 항목 하나의 판정 기준 — (위치, 그 항목 URL 자신의 문서 id 이름과 정수 값)."""

    located = _document_location(url)
    if located is None:
        raise ValueError(f"목록 URL을 해석할 수 없다: {url}")
    location, pairs = located
    ids: dict[str, int] = {}
    for name, value in pairs:
        if name in CURATED_DOCUMENT_ID_PARAMS:
            number = _document_id_value(value, name=name)
            if number is None or name in ids:
                raise ValueError(f"목록 URL의 문서 id가 하나의 정수가 아니다: {url}")
            ids[name] = number
    return location, tuple(sorted(ids.items()))


def _document_index(entries):  # type: ignore[no-untyped-def]
    index: dict[str, list[tuple[tuple[tuple[str, int], ...], object]]] = {}
    for url, value in entries:
        location, ids = _document_matcher(str(url))
        index.setdefault(location, []).append((ids, value))
    return index


def _matching_documents(url: object, index, *, first_value_only: bool = False) -> list:  # type: ignore[no-untyped-def]
    """이 주소가 가리킬 수 있는 목록 항목들.

    위치가 같고, 항목 URL이 쓰는 문서 id 이름마다 이 주소에 그 이름의 값이 있어 그 값을 읽은
    정수(`_document_id_value`)가 항목의 값과 같으면 그 항목이다. 항목이 쓰지 않는 id 이름
    (`contentId`·`SEQ` …)은 서버도 읽지 않으므로 보지 않는다.

    같은 이름이 여러 번 오면 서버는 첫 값을 쓴다. `first_value_only`는 그 첫 값만 본다 — 목록
    문서로 인정해 주는 쪽(의료 글의 카탈로그 주제 대조·장애 시 유지)이 쓴다. 기본은 상한 없이
    모든 값을 본다 — 목록 문서라서 빼는 쪽(진료비·병원 선택 글·PATCH 422·제외 목록)이 쓴다.
    여러 항목과 맞으면 모두 돌려준다.
    """

    located = _document_location(url)
    if located is None:
        return []
    location, pairs = located
    candidates = index.get(location)
    if not candidates:
        return []
    values: dict[str, set[int]] = {}
    seen: set[str] = set()
    for name, value in pairs:
        if name in CURATED_DOCUMENT_ID_PARAMS:
            if first_value_only and name in seen:
                continue
            seen.add(name)
            number = _document_id_value(value, name=name)
            if number is not None:
                values.setdefault(name, set()).add(number)
    return [
        entry
        for ids, entry in candidates
        if all(number in values.get(name, ()) for name, number in ids)
    ]


_EXCLUDED_DOCUMENTS = _document_index(
    (entry["url"], entry["reason"]) for entry in REFERENCE_URL_EXCLUSIONS
)


def reference_exclusion_reason(url: object) -> str | None:
    """제외 목록에 있는 문서(별칭 포함)면 그 사유, 아니면 None."""
    reasons = _matching_documents(url, _EXCLUDED_DOCUMENTS)
    return str(reasons[0]) if reasons else None


_CURATED_DOCUMENTS = _document_index(
    (source["url"], source) for source in CURATED_MEDICAL_SOURCE_PAGES
)


def curated_source_entries(url: object) -> list[dict[str, object]]:
    """이 주소로 서버가 돌려주는 수기 목록 항목들 — 같은 문서의 별칭도 그 항목이다.

    반복된 id는 서버처럼 첫 값만 본다(`first_value_only`). 목록 문서로 인정해 주는 판정
    (카탈로그 주제 대조·검증기의 `curated`)이 쓴다 — `cntnts_sn=1&cntnts_sn=3796`은 요통 문서가
    아니다. 빼는 판정은 `is_curated_source_url`이다.
    """

    return list(_matching_documents(url, _CURATED_DOCUMENTS, first_value_only=True))


def is_curated_source_url(url: object) -> bool:
    """수기 목록 문서일 수 있는가 — 같은 문서의 별칭, 반복된 id의 어느 한 값이 목록 문서인
    주소도 목록 문서다. 진료비·병원 선택 글에서 빼고 PATCH가 422로 거절하는 쪽이 쓴다."""
    return bool(_matching_documents(url, _CURATED_DOCUMENTS))


_INSTITUTION_TITLE_TOKENS: frozenset[str] = frozenset(
    re.sub(r"[^0-9a-z가-힣]+", "", part.lower())
    for name in _DOMAIN_TO_INSTITUTION_NAME.values()
    for part in name.split()
    if re.sub(r"[^0-9a-z가-힣]+", "", part.lower())
)


def institution_title_tokens() -> frozenset[str]:
    """기관 이름을 이루는 토큰들(정규화) — 참고자료 주제 판정에서 제외할 단어.

    '질병관리청 국가건강정보포털 - 대장암'에서 주제를 결정하는 토큰은 '대장암'뿐이다.
    기관명은 어느 글에 붙어도 같으므로 적합성 점수의 분모에서 빼야 한다.
    """
    return _INSTITUTION_TITLE_TOKENS


def institution_label_for_url(url: str) -> str | None:
    """화이트리스트 URL을 기관 이름 기반의 중립 표기로 되돌린다.

    참고자료 제목은 URL과 달리 모델의 자유 텍스트다. 공신력 도메인 문서에 광고 문구
    제목("부작용 없는 치료 안내")이 붙어도 그대로 두면 공개 표면과 JSON-LD
    citation.name 으로 나간다. 제목만 기관 표기로 바꾸면 근거(URL)는 지키면서 그
    노출을 없앨 수 있다. 화이트리스트 밖 URL은 애초에 참고자료로 인정하지 않으므로
    None 을 돌려준다.
    """
    hostname = _extract_hostname(url)
    if not hostname:
        return None
    matched = [
        domain for domain in _DOMAIN_TO_INSTITUTION_NAME if _matches_domain(hostname, domain)
    ]
    if not matched:
        return None
    # 가장 구체적인 도메인(health.kdca.go.kr > kdca.go.kr)의 이름을 쓴다.
    name = _DOMAIN_TO_INSTITUTION_NAME[max(matched, key=len)]
    return f"{name} 자료"


def is_citable_reference_url(url: str) -> bool:
    """공신력 도메인의 특정 자료 URL인지 확인한다.

    기관 홈페이지 루트는 기관의 권위만 보여줄 뿐 콘텐츠의 개별 주장 근거가 아니다.
    실제 문서 경로나 문서 식별 query가 있는 URL만 발행 근거로 인정한다.
    """
    if not is_whitelisted_url(url):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.path not in {"", "/"} or bool(parsed.query)


def select_curated_authority_sources(text: str, *, limit: int = 3) -> list[dict[str, str]]:
    """본문 주제와 일치하는, 사람이 검증한 특정 권위 문서를 반환한다."""
    compact = re.sub(r"[\W_]+", "", (text or "").lower())
    if not compact or limit <= 0:
        return []

    selected: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for source in CURATED_MEDICAL_SOURCE_PAGES:
        # 진료과 이름·병원 고르기 경로 키워드는 주제가 아니다(`keyword_names_provider`).
        keywords = [
            keyword for keyword in source["keywords"] if not keyword_names_provider(keyword)
        ]
        if not any(str(keyword).lower() in compact for keyword in keywords):
            continue
        url = str(source["url"])
        if url in seen_urls or reference_exclusion_reason(url) is not None:
            continue
        selected.append(
            {
                "title": str(source["title"]),
                "url": url,
                "source_type": infer_source_type(url) or SOURCE_TYPE_ACADEMIC_KR,
            }
        )
        seen_urls.add(url)
        if len(selected) >= limit:
            break
    return selected


def render_source_hint_block() -> str:
    """프롬프트에 주입할 권위 출처 안내 텍스트."""
    lines = [
        "[참고 출처 화이트리스트 — references는 아래 도메인만 사용]",
        "- 기관 홈페이지 루트 URL은 근거가 아닙니다. 주장을 실제로 담은 특정 문서 URL만 쓰세요.",
        "- [현재 주제와 일치하는 검증된 문서]가 함께 주어지면 **그 URL을 그대로** 쓰세요. "
        "사람이 제목과 주소를 확인한 문서입니다.",
        "- 문서 번호(cntnts_sn·contentId 등)나 메뉴 코드를 추측해 URL을 지어내지 마세요. "
        "확실하지 않은 항목은 빼세요 — 추측한 주소는 대부분 없는 문서이거나 다른 질환 문서입니다.",
        "- 목록 밖 URL은 시스템이 실제로 열어 제목·본문이 이 글의 주제와 맞는지 확인한 것만 남깁니다. "
        "남는 출처가 없으면 검증된 목록에서 채우고, 그래도 없으면 발행을 보류합니다. "
        "본문에서 확인할 수 없는 주장·수치는 제거합니다.",
    ]
    for label, group in (
        ("한국 공공", KR_PUBLIC_SOURCES),
        ("한국 학술", KR_ACADEMIC_SOURCES),
        ("국제 의료", US_GLOBAL_SOURCES),
        ("백과", ENCYCLOPEDIA_SOURCES),
    ):
        lines.append(f"- {label}:")
        for item in group:
            lines.append(f"  - {item['name']} ({item['domain']})")
    return "\n".join(lines)
