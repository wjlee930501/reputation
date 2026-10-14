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

# 구분자: 하이픈·유니코드 대시(‐‑‒–—―−－)·점·공백 여러 칸. 문서·PDF 추출은 대시를 en dash로, 칸을 여러
# 공백으로 바꾸기 일쑤다. 본문 자체는 정규화하지 않는다(NFKC는 한글 호환 자모 등 원문 표기를 바꾼다) —
# 전각 숫자·기호(０-９＋－＠)만 반각으로 바꾼 뒤(길이 보존) 정규식이 나머지 표기를 받는다.
_FULLWIDTH = str.maketrans("０１２３４５６７８９＋－＠", "0123456789+-@")
_DASHES = "\\-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d"
_SEP = f"[{_DASHES}.\\s]*"
# 휴대전화: 010-1234-5678, 01012345678, 010 1234 5678, 010–1234–5678, +82 10-1234-5678. 지역번호(02·031…)는
# 병원 대표번호라 가리지 않는다.
_MOBILE_RE = re.compile(
    rf"(?<!\d)(?:(?:[+＋]?82{_SEP})[(]?0?1[016789]|01[016789])[)]?{_SEP}\d{{3,4}}{_SEP}\d{{4}}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# 주민등록번호: 생년월일 6자리 + 성별 1~4로 시작하는 7자리(구분자는 대시류·공백).
_RRN_RE = re.compile(
    rf"(?<!\d)\d{{2}}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[{_DASHES}\s]*[1-4]\d{{6}}(?!\d)"
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
    masked = _RRN_RE.sub(MASK, text.translate(_FULLWIDTH))
    masked = _EMAIL_RE.sub(MASK, masked)
    return _MOBILE_RE.sub(MASK, masked)


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
