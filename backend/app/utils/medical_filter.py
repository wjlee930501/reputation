"""의료광고 금지 표현 필터 — 전체 경로 공통"""
import re

# 기본 금지 표현 (표시용)
FORBIDDEN_EXPRESSIONS = [
    "1등", "최고", "최우수", "유일", "완치", "100%",
    "성공률", "부작용 없는", "검증된", "가장 잘하는",
    "국내 최초", "세계 최초", "특허", "독보적",
]

# 정규식 패턴 (변형 포착) — content_engine.py에서 이관
FORBIDDEN_PATTERNS: dict[str, re.Pattern] = {
    "1등": re.compile(r"1등|일등|1위|일위"),
    "최고": re.compile(r"최고[의]?|최상[의]?"),
    "최우수": re.compile(r"최우수"),
    "유일": re.compile(r"유일[한]?|유일무이"),
    "완치": re.compile(r"완치[율]?|완전\s*치료"),
    "100%": re.compile(r"100\s*%|백\s*퍼센트"),
    "성공률": re.compile(r"성공률|성공\s*확률"),
    "부작용 없는": re.compile(r"부작용\s*(없|zero|제로)"),
    "검증된": re.compile(r"검증[된]?|입증[된]?"),
    "가장 잘하는": re.compile(r"가장\s*(잘|뛰어)"),
    "국내 최초": re.compile(r"(국내|세계|아시아)\s*최초"),
    "세계 최초": re.compile(r"세계\s*최초"),
    "특허": re.compile(r"특허[를]?\s*(보유|획득|취득)"),
    "독보적": re.compile(r"독보적[인]?"),
}


def check_forbidden(text: str) -> list[str]:
    """텍스트에서 의료광고 금지 표현을 찾아 매칭된 기본 표현 목록을 반환."""
    if not text:
        return []
    found = []
    for label, pattern in FORBIDDEN_PATTERNS.items():
        if pattern.search(text):
            found.append(label)
    return found
