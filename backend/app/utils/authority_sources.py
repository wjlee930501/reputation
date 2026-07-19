"""의료 콘텐츠 인용용 권위 출처 화이트리스트.

GEO 논문(Princeton·Georgia Tech 2024)·5W Citation Source Audit Q1 2026·
SE Ranking YMYL Health Study(2025) 등에서 AI 답변(ChatGPT/Gemini/Perplexity)
인용 가중치가 입증된 도메인만 추립니다. 의료광고법(제56조) 광고 유인성을
유발하지 않는 비영리·공공·학술 출처만 포함합니다.

용도:
- content_engine.py 프롬프트에 주입해 인용 후보를 제한.
- _normalize_references 단계에서 white-list domain 외 항목 검출(선택).
"""

from urllib.parse import urlparse

KR_PUBLIC_SOURCES: list[dict[str, str]] = [
    {"name": "질병관리청 국가건강정보포털", "domain": "health.kdca.go.kr"},
    {"name": "질병관리청 KDCA", "domain": "kdca.go.kr"},
    {"name": "국가암정보센터", "domain": "cancer.go.kr"},
    {"name": "건강보험심사평가원 HIRA", "domain": "hira.or.kr"},
    {"name": "보건복지부", "domain": "mohw.go.kr"},
    {"name": "식품의약품안전처 MFDS", "domain": "mfds.go.kr"},
    {"name": "의료기관 평가인증원 KOIHA", "domain": "koiha.or.kr"},
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

# 브라우징 없이 생성하는 모델에게 URL을 추측시키면 존재하는 다른 질환 문서나 기관
# 홈페이지가 인용되는 문제가 생긴다. 아래 목록은 사람이 실제 제목과 URL을 확인한
# 특정 문서만 담는 작은 신뢰 카탈로그다. 키워드가 맞는 문서가 있을 때는 모델이 만든
# URL보다 이 목록을 우선한다.
CURATED_MEDICAL_SOURCE_PAGES: tuple[dict[str, object], ...] = (
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
        "keywords": ("대장내시경", "내시경", "장정결"),
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
        "title": "국가암정보센터 — 대장암",
        "url": "https://cancer.go.kr/lay1/program/S1T211C223/cancer/view.do?cancer_seq=3797",
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
        hostname = urlparse(url).hostname
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
    compact = "".join((text or "").lower().split())
    if not compact or limit <= 0:
        return []

    selected: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for source in CURATED_MEDICAL_SOURCE_PAGES:
        keywords = source["keywords"]
        if not any(str(keyword).lower() in compact for keyword in keywords):
            continue
        url = str(source["url"])
        if url in seen_urls:
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
        "- 정확한 문서 URL을 모르면 추측하지 말고 references를 비워 두세요. 시스템이 검증된 문서를 보완합니다.",
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
