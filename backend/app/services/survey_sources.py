"""원장 인터뷰 설문 자료(D32) — 개인정보 가림(E6)과 공유 링크 정규화.

설문은 계약 때 PDF로 오거나 Google 문서·스프레드시트 링크로 온다. 기존 근거 자료 경로
(`SourceType.INTERVIEW`, PDF 업로드·URL 크롤)를 그대로 쓰고, 여기서는 그 경로에 빠져 있던 두 가지만 더한다.

- **가림(E6):** 설문 본문은 근거 노트를 거쳐 Essence의 공개 about 서사로 나갈 수 있다. 저장 전에
  휴대전화·이메일·주민등록번호 형태를 ``[가림]``으로 바꾼다. 병원 대표번호(지역번호)·진료시간·날짜
  숫자는 그대로 둔다.
- **링크:** Google 문서는 텍스트 내보내기, 스프레드시트는 CSV 내보내기 주소로 바꿔 읽는다. 링크 공유가
  꺼져 있으면 Google이 로그인 페이지로 보내므로 fetch가 그 리다이렉트를 실패로 돌린다
  (`asset_extractor.GOOGLE_LOGIN_REQUIRED_MESSAGE`). Google 설문지 링크는 응답이 아니라 질문지만
  보이므로 받지 않는다.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from app.models.essence import SourceType

MASK = "[가림]"

# 맞추기 전에 전각 ASCII(U+FF01–U+FF5E)를 반각으로, 전각 공백(U+3000)을 공백으로 바꾼다(길이 보존). 본문 전체에
# NFKC는 걸지 않는다 — 한글 호환 자모 등 원문 표기까지 바꾼다.
_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)} | {0x3000: 0x20}
# 숫자 사이 구분자: 하이픈·유니코드 대시·점·괄호·공백 여러 칸. 문서·PDF 추출은 대시를 en dash로, 칸을
# 여러 공백으로 바꾸기 일쑤라 **모든 숫자 사이**에 허용하고 식별자 전체를 가린다(넓게 가린다 — fail-closed).
_DASHES = "\\-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_S = f"[{_DASHES}.()\\s]*"
# 휴대전화: 01x + 7~8자리, 국제번호 +82(0)1x. 지역번호(02·031…)는 병원 대표번호라 가리지 않는다.
_MOBILE_RE = re.compile(
    rf"(?<!\d)\(?(?:\+?{_S}8{_S}2{_S}(?:0{_S})?|0{_S})1{_S}[016789](?:{_S}\d){{7,8}}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# 주민등록번호: 생년월일 6자리(월 01–12·일 01–31) + 성별 1~4로 시작하는 7자리, 모든 숫자 사이 구분자 허용.
_RRN_RE = re.compile(
    rf"(?<![\d+])\d{_S}\d{_S}(?:0{_S}[1-9]|1{_S}[0-2]){_S}(?:0{_S}[1-9]|[12]{_S}\d|3{_S}[01])"
    rf"{_S}[1-4](?:{_S}\d){{6}}(?!\d)"
)

SURVEY_DEFAULT_TITLE = "원장 인터뷰 설문"
GOOGLE_FORMS_MESSAGE = (
    "Google 설문지 링크로는 응답을 읽을 수 없습니다. 설문 응답은 PDF(응답 → 인쇄 → PDF 저장)나 "
    "연결된 스프레드시트 링크로 올려 주세요."
)

_DOC_RE = re.compile(r"^/document/d/([A-Za-z0-9_-]+)")
_SHEET_RE = re.compile(r"^/spreadsheets/d/([A-Za-z0-9_-]+)")


def mask_personal_info(text: str | None) -> str | None:
    """휴대전화·이메일·주민등록번호 형태를 ``[가림]``으로 바꾼다."""

    if not text:
        return text
    # 주민번호를 먼저 — "80 01 01-1…"의 뒷부분이 휴대전화(01x)로 먼저 잘려 앞자리가 남지 않게. 국제번호
    # (+82)는 주민번호 시작으로 보지 않아 휴대전화 정규식이 '+'까지 가린다.
    masked = _RRN_RE.sub(MASK, text.translate(_FULLWIDTH))
    masked = _MOBILE_RE.sub(MASK, masked)
    return _EMAIL_RE.sub(MASK, masked)


def mask_interview_text(source_type: SourceType | str | None, text: str | None) -> str | None:
    """설문(INTERVIEW) 자료의 본문만 가린다. 다른 자료는 그대로 돌려준다."""

    if str(getattr(source_type, "value", source_type)) != SourceType.INTERVIEW.value:
        return text
    return mask_personal_info(text)


class SurveyLinkError(ValueError):
    """받지 않는 설문 링크. 메시지는 운영자에게 그대로 보여 줄 수 있다."""


def normalize_survey_url(url: str) -> str:
    """Google 문서·스프레드시트 공유 링크를 내보내기 주소로 바꾼다. 그 밖의 주소는 그대로다.

    Google 설문지(``docs.google.com/forms``·``forms.gle``) 링크는 :class:`SurveyLinkError`.
    """

    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    if host == "forms.gle" or (host == "docs.google.com" and parsed.path.startswith("/forms/")):
        raise SurveyLinkError(GOOGLE_FORMS_MESSAGE)
    if host != "docs.google.com":
        return cleaned
    doc = _DOC_RE.match(parsed.path)
    if doc:
        return f"https://docs.google.com/document/d/{doc.group(1)}/export?format=txt"
    sheet = _SHEET_RE.match(parsed.path)
    if sheet:
        gid = parse_qs(parsed.query).get("gid", [None])[0]
        if gid is None and parsed.fragment.startswith("gid="):
            gid = parsed.fragment[len("gid=") :]
        suffix = f"&gid={gid}" if gid and gid.isdigit() else ""
        return f"https://docs.google.com/spreadsheets/d/{sheet.group(1)}/export?format=csv{suffix}"
    return cleaned
