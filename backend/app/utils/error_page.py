"""차단·오류 페이지 잔재 판정 — 수집·조립·공개 3계층 공용.

Jina Reader 등 폴백 리더는 차단 페이지(403/404 등)도 HTTP 200으로 돌려주며,
본문 첫 줄에 원본 <title>을 ``Title: 403 Forbidden`` 형태로 그대로 노출한다.
이 잔재가 근거 자료로 수집되면 essence 파이프라인이 철학 핵심 메시지로 조립하고,
승인 후 public_about까지 흘러가 공개 페이지에 오류 문구가 노출된다(실증 버그).

세 계층(수집 fetch / 조립 essence / 공개 site)이 동일 판정을 쓰도록 판정 로직을 이 한 곳에 둔다.
정상 제목(예: ``Title: 병원 소개``)은 오탐하지 않도록, ``Title:`` 세그먼트에
오류 신호가 실제로 있을 때만 True를 반환한다.
"""
from __future__ import annotations

import re

# Jina Reader 등이 남기는 제목 접두. "Title:" 뒤 한 줄(최대 80자)을 오류 신호 검사 대상으로 본다.
_TITLE_SEGMENT_RE = re.compile(r"Title:\s*([^\n]{0,80})", re.IGNORECASE)

# 오류/차단 페이지 제목에 나타나는 텍스트 신호 (소문자 비교). 숫자 코드만으로는 판정하지 않아
# "500가지 건강 상식" 같은 정상 제목을 오탐하지 않는다.
_ERROR_PAGE_PHRASES: tuple[str, ...] = (
    "forbidden",
    "access denied",
    "not found",
    "unauthorized",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "service unavailable",
    "service temporarily unavailable",
    "too many requests",
    "attention required",
    "just a moment",
    "차단",
    "접근이 거부",
    "접근 거부",
    "접근할 수 없",
    "접근이 제한",
    "권한이 없",
    "이용할 수 없",
    "요청이 거부",
)

# "403 Forbidden", "404 Not Found", "500 오류"처럼 HTTP 상태코드가 오류 단어와 붙어 나오는 형태.
# 상태코드 숫자는 반드시 오류 단어와 인접해야 하므로 "500가지" 같은 정상 제목과 구분된다.
_HTTP_ERROR_STATUS_RE = re.compile(
    r"\b(4\d\d|5\d\d)\b\s*[-–—:|]?\s*"
    r"(forbidden|not\s*found|unauthorized|bad\s*gateway|gateway\s*timeout|"
    r"internal\s*server\s*error|service\s*unavailable|too\s*many|error|오류|에러|차단)",
    re.IGNORECASE,
)


def _segment_is_error(segment: str) -> bool:
    """``Title:`` 세그먼트가 오류 신호를 담는지 판정한다."""
    lowered = segment.lower()
    if any(phrase in lowered for phrase in _ERROR_PAGE_PHRASES):
        return True
    return bool(_HTTP_ERROR_STATUS_RE.search(segment))


def looks_like_error_page_text(text: str | None) -> bool:
    """텍스트가 차단·오류 페이지 잔재(``Title: 403 Forbidden`` 등)를 포함하는지 판정한다.

    ``Title:`` 세그먼트가 알려진 오류 신호(Forbidden/Not Found/차단/HTTP 상태코드+오류 단어 등)를
    담고 있으면 True. 무해한 제목(``Title: 병원 소개``)은 False.
    """
    if not text:
        return False
    return any(_segment_is_error(match.group(1)) for match in _TITLE_SEGMENT_RE.finditer(text))
