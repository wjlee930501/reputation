"""참고자료가 필수인 글인가 — 생성·프롬프트·스키마·수기 목록 치유·모든 발행 게이트의 한 규칙.

규칙(2026-09-29, 2af00d02 원인 후속):

- `REFERENCES_REQUIRED_TYPES`(FAQ·DISEASE·TREATMENT·COLUMN·HEALTH·LOCAL)는 항상 필수다.
- NOTICE는 **의료 주제를 실었을 때** 필수다. 의료 주제의 증거는 측정 질문 연결이다:
  `query_target_id`가 있거나, 브리프의 `query_target`(이름·질환·시술·진료과 중 하나라도)이나
  `exposure_action.query_target_id`가 있다. 노출 계획(`source.mode == automatic_exposure_plan`)이
  고른 질문이 곧 이 연결이다.
- 순수 운영 공지(질문 연결 없음)는 면제다. 5821409e('NOTICE 영구 미발행') — 생성은 참고자료를
  요구하지 않는데 발행만 요구해 공지가 매일 MISSING_REFERENCES로 막히다 영구 DRAFT가 된
  회귀 — 를 되살리지 않는다.

브리프의 `target_keyword`·`treatment_narrative`·`source.mode`만으로는 판정하지 않는다. 노출
계획은 질문을 고르지 못한 슬롯에도 `automatic_exposure_plan`을 찍고, 그때 `target_keyword`는
슬롯 제목(예: "NOTICE content slot")에서, `treatment_narrative`는 병원 첫 진료 항목에서 채워진다 —
그 값으로 판정하면 모든 공지가 필수가 되어 위 회귀가 돌아온다. 질문이 연결된 브리프에서는 그
값들이 질문의 질환·시술·진료과에서 오므로 위 연결 규칙이 같은 글을 잡는다.

2af00d02: NOTICE + 질문 f1de79a2('마포구 신경외과 병원 추천해줘', 진료과 신경외과) — 이 규칙으로
필수다. 모델이 쓴 도메인 루트 URL 2개가 빠져 0개가 되면 생성은 수기 목록 치유 또는 참고자료
거절, 발행은 치유 또는 MISSING_REFERENCES 보류가 된다.

이 모듈은 가벼워야 한다 — content_engine·content_publication·reference_publication이 모두 읽는다.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models.content import ContentType

# 참고 자료가 반드시 필요한 콘텐츠 유형. **생성 검증과 발행 게이트가 같은 값을 써야 한다** —
# 따로 두면 생성은 통과하고 발행만 막혀 슬롯이 영구히 비는 유형이 생긴다(NOTICE가 그랬다).
# NOTICE는 여기 없다 — 의료 주제를 실은 공지만 `references_required_for`가 필수로 올린다.
REFERENCES_REQUIRED_TYPES: frozenset = frozenset(
    {
        ContentType.FAQ,
        ContentType.DISEASE,
        ContentType.TREATMENT,
        ContentType.COLUMN,
        ContentType.HEALTH,
        ContentType.LOCAL,
    }
)

_REQUIRED_TYPE_VALUES = frozenset(content_type.value for content_type in REFERENCES_REQUIRED_TYPES)
# 브리프의 질문 참조에서 의료 주제를 말하는 칸.
_QUERY_TARGET_TOPIC_FIELDS = ("id", "name", "treatment", "condition_or_symptom", "specialty")


def _type_value(content_type: object) -> str:
    return str(getattr(content_type, "value", content_type) or "").upper()


def _filled(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def brief_carries_medical_topic(content_brief: object) -> bool:
    """브리프가 측정 질문(의료 주제)에 연결돼 있는가."""

    if not isinstance(content_brief, Mapping):
        return False
    query_target = content_brief.get("query_target")
    if isinstance(query_target, Mapping) and any(
        _filled(query_target.get(field)) for field in _QUERY_TARGET_TOPIC_FIELDS
    ):
        return True
    exposure_action = content_brief.get("exposure_action")
    return isinstance(exposure_action, Mapping) and _filled(
        exposure_action.get("query_target_id")
    )


def references_required_for(
    content_type: object,
    *,
    content_brief: object = None,
    query_target_id: object = None,
) -> bool:
    """이 유형·브리프·질문 연결의 글에 참고자료가 필수인가. 유형을 못 읽으면 필수로 둔다."""

    value = _type_value(content_type)
    if not value:
        return True
    if value in _REQUIRED_TYPE_VALUES:
        return True
    if value == ContentType.NOTICE.value:
        return _filled(query_target_id) or brief_carries_medical_topic(content_brief)
    return False


def references_required(item: object) -> bool:
    """행(또는 같은 속성을 가진 보기)에 대한 `references_required_for`."""

    return references_required_for(
        getattr(item, "content_type", None),
        content_brief=getattr(item, "content_brief", None),
        query_target_id=getattr(item, "query_target_id", None),
    )
