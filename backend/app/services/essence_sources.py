"""콘텐츠 운영 기준의 "필수 텍스트 자료" 경계 — readiness·자동 검수·승인·노이즈 hash가 모두 이 predicate를 쓴다.

제외되지 않은, 사진이 아닌, **원문이 있는** 자료만 필수다. URL만 있고 본문이 없는 자료는 추출할
것이 없으므로 처리도 승인도 막지 않는다(M-01). 크롤·업로드로 본문이 생기는 순간 PENDING 필수 자료가
되어 승인이 stale이 된다. 모델만 import한다 — 순환 import를 피하기 위해 다른 서비스는 여기서 가져간다.
"""

from __future__ import annotations

from sqlalchemy import and_, func

from app.models.essence import PHOTO_SOURCE_TYPES, HospitalSourceAsset, SourceStatus

# btrim은 인자가 하나면 공백만 깎는다. 줄바꿈·탭만 있는 본문도 "없음"으로 봐야 하므로
# Python의 str.strip()과 같은 공백 집합을 명시한다.
_BLANK_CHARS = " \t\n\r\f\v"


def required_text_source_predicate():
    return and_(
        HospitalSourceAsset.status != SourceStatus.EXCLUDED,
        HospitalSourceAsset.source_type.notin_(list(PHOTO_SOURCE_TYPES)),
        HospitalSourceAsset.raw_text.isnot(None),
        func.length(func.btrim(HospitalSourceAsset.raw_text, _BLANK_CHARS)) > 0,
    )
