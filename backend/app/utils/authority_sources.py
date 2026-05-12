"""의료 콘텐츠 인용용 권위 출처 화이트리스트.

GEO 논문(Princeton·Georgia Tech 2024)·5W Citation Source Audit Q1 2026·
SE Ranking YMYL Health Study(2025) 등에서 AI 답변(ChatGPT/Gemini/Perplexity)
인용 가중치가 입증된 도메인만 추립니다. 의료광고법(제56조) 광고 유인성을
유발하지 않는 비영리·공공·학술 출처만 포함합니다.

용도:
- content_engine.py 프롬프트에 주입해 인용 후보를 제한.
- _normalize_references 단계에서 white-list domain 외 항목 검출(선택).
"""

KR_PUBLIC_SOURCES: list[dict[str, str]] = [
    {"name": "질병관리청 국가건강정보포털", "domain": "health.kdca.go.kr"},
    {"name": "질병관리청 KDCA", "domain": "kdca.go.kr"},
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


def is_whitelisted_url(url: str) -> bool:
    """URL이 권위 출처 화이트리스트에 속하는지 검사. 서브도메인 매칭 포함."""
    if not url:
        return False
    lowered = url.lower()
    for domain in WHITELIST_DOMAINS:
        if f"//{domain}" in lowered or f".{domain}" in lowered:
            return True
    return False


def render_source_hint_block() -> str:
    """프롬프트에 주입할 권위 출처 안내 텍스트."""
    lines = ["[참고 출처 화이트리스트 — references는 아래 도메인만 사용]"]
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
