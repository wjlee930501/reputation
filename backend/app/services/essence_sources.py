"""콘텐츠 운영 기준의 "필수 텍스트 자료" 경계 — readiness·자동 검수·승인·노이즈 hash가 모두 이 predicate를 쓴다.

제외되지 않은, 사진이 아닌, **원문이 있는** 자료만 필수다. URL만 있고 본문이 없는 자료는 추출할
것이 없으므로 처리도 승인도 막지 않는다(M-01). 크롤·업로드로 본문이 생기는 순간 PENDING 필수 자료가
되어 승인이 stale이 된다. 모델만 import한다 — 순환 import를 피하기 위해 다른 서비스는 여기서 가져간다.
"""

from __future__ import annotations

from sqlalchemy import and_, func
from sqlalchemy.sql.elements import ColumnElement

from app.models.essence import PHOTO_SOURCE_TYPES, HospitalSourceAsset, SourceStatus

# btrim은 인자가 하나면 공백만 깎는다. 줄바꿈·탭만 있는 본문도 "없음"으로 봐야 하므로
# Python의 str.strip()과 같은 공백 집합을 그대로 적는다. 워커는 .strip()이 빈 문자열이면
# 처리를 거부하므로, 여기서 집합이 좁으면 NBSP·전각 공백만 있는 본문이 "필수인데 처리 불가"가 된다.
_BLANK_CHARS = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def required_text_source_predicate() -> ColumnElement[bool]:
    return and_(
        HospitalSourceAsset.status != SourceStatus.EXCLUDED,
        HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
        HospitalSourceAsset.raw_text.isnot(None),
        func.length(func.btrim(HospitalSourceAsset.raw_text, _BLANK_CHARS)) > 0,
    )
