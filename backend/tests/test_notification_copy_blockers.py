"""아침·주간 요약의 차단 라벨은 코드와 정확히 맞춘다(#179 2차 비차단 3).

예전 `blocker_copy`는 "UNAVAILABLE" 부분 문자열로 검수 라벨을 골라 콘텐츠 생성 서비스 장애
(`PROVIDER_UNAVAILABLE`)까지 '자동 검수 미완료'로 불렀다. 검수 라벨은 독립 검수 코드에만,
생성 서비스 장애는 자기 라벨에 붙는다. 요약에 들어올 수 있는 코드(아침 요약·주간 요약·#177
기관 사이트 보류·운영자 판단 문구 키)마다 라벨을 고정한다.
"""

from __future__ import annotations

import re

import pytest

from app.services.notification_copy import (
    REFERENCES_OPERATOR_DECIDES_COPY_CODE,
    REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE,
    blocker_copy,
)
from app.workers.generation_incident_control import (
    _MORNING_DIGEST_ONLY_CODES,
    _MORNING_GENERATION_NOTIFICATION_CODES,
    WEEKLY_REJECTED_GENERATION_CODES,
)
from app.workers.generation_retry_policy import GenerationRetryClass, retry_class_for

_REVIEW_PENDING = "자동 검수 미완료"
_PROVIDER_OUTAGE = "생성 서비스 일시 장애"
_GENERATION_ERROR = "생성 서비스 오류"
_BODY_REVIEW = "본문·근거 확인 필요"
_IMAGE = "발행용 이미지 준비 실패"

# 요약에 실릴 수 있는 모든 코드의 라벨(고친 뒤). 새 코드가 요약에 들어오면 여기서 라벨을 정한다.
EXPECTED_TITLES = {
    # 아침 요약(07:45·08:00)
    "PROVIDER_TIMEOUT": _PROVIDER_OUTAGE,
    "PROVIDER_UNAVAILABLE": _PROVIDER_OUTAGE,
    "GENERATION_FAILED": _GENERATION_ERROR,
    "CONTENT_NOT_GENERATED": "발행용 원고 미생성",
    "GENERATION_LEASE_ACTIVE": _BODY_REVIEW,
    "STALE_GENERATION_CLAIM": _BODY_REVIEW,
    "CONTENT_IMAGE_NOT_READY": _IMAGE,
    "CONTENT_IMAGE_NOT_VERIFIED": _IMAGE,
    "IMAGE_GENERATION_FAILED": _IMAGE,
    "IMAGE_GENERATION_RETRIES_EXHAUSTED": _IMAGE,
    "MISSING_APPROVED_ESSENCE": "운영 기준 미승인",
    # 주간 요약
    "GENERATION_REJECTED": _BODY_REVIEW,
    "FAQ_FIELDS_MISSING": _BODY_REVIEW,
    "MISSING_REFERENCES": _BODY_REVIEW,
    "FORBIDDEN_EXPRESSION": _BODY_REVIEW,
    "ESSENCE_NOT_ALIGNED": _BODY_REVIEW,
    "CONTENT_AI_HARD_FINDING": _BODY_REVIEW,
    "CONTENT_AI_REVIEW_STALE": _BODY_REVIEW,
    "CONTENT_IMAGE_POLICY_REJECTED": _IMAGE,
    # 23:00 발행기가 직접 싣는 코드와 문구 키
    "REFERENCE_SITE_UNREACHABLE": "기관 사이트 접속 불가로 발행 대기",
    "TOPIC_SWAPPED": "주제 자동 교체",
    "COST_BLOCKED": "자동 작업 비용 한도 도달",
    "CONTENT_AI_REVIEW_UNAVAILABLE": _REVIEW_PENDING,
    REFERENCES_OPERATOR_DECIDES_COPY_CODE: "참고 자료 운영자 판단",
    REFERENCES_OPERATOR_DECIDES_UNWRITTEN_COPY_CODE: "참고 자료 운영자 판단",
}


def test_the_table_covers_every_code_a_digest_can_carry():
    carried = (
        _MORNING_GENERATION_NOTIFICATION_CODES
        | _MORNING_DIGEST_ONLY_CODES
        | WEEKLY_REJECTED_GENERATION_CODES
    )
    assert carried <= set(EXPECTED_TITLES)


@pytest.mark.parametrize("code", sorted(EXPECTED_TITLES))
def test_each_digest_code_gets_its_own_label(code):
    assert blocker_copy(code).title == EXPECTED_TITLES[code]


def test_a_provider_outage_is_not_called_an_unfinished_review():
    for code in ("PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"):
        copy = blocker_copy(code)
        assert copy.title == _PROVIDER_OUTAGE
        assert copy.title != _REVIEW_PENDING
        assert "검수" not in copy.action
        # #182 뒤에는 환경 실패 기록의 빈 슬롯도 “작업 다시 시도”가 바로 푼다.
        assert re.findall(r"“([^”]+)”", copy.action) == ["작업 다시 시도"]
    assert blocker_copy("PROVIDER_UNAVAILABLE") == blocker_copy("PROVIDER_TIMEOUT")


def test_a_generation_error_is_a_service_error_not_a_body_review():
    """GENERATION_FAILED는 환경 실패(ENVIRONMENT_RECOVERABLE)다 — 본문·근거 확인이 아니다(#187 2차 s2).

    빈 슬롯의 환경 실패 기록은 “작업 다시 시도”가 억제를 풀어 바로 다시 시도한다
    (`test_generation_incident_copy_retry`의 ENV-GENERATION_FAILED).
    """

    assert retry_class_for("GENERATION_FAILED") == GenerationRetryClass.ENVIRONMENT_RECOVERABLE
    copy = blocker_copy("GENERATION_FAILED")
    assert copy.title == _GENERATION_ERROR
    assert copy.title != _BODY_REVIEW
    assert copy.action == (
        "콘텐츠 생성 작업이 오류로 중단돼 원고를 만들지 못했습니다. 운영 센터에서 해당 글의 생성 "
        "상태를 확인하고, 오류가 풀렸으면 “작업 다시 시도”를 눌러 주세요."
    )
    assert copy.button == "생성 상태 확인"


def test_the_provider_outage_digest_spells_the_operations_center_with_a_space():
    assert blocker_copy("PROVIDER_TIMEOUT").action == (
        "콘텐츠 생성 서비스의 일시 장애로 원고를 만들지 못했습니다. 운영 센터에서 해당 글의 생성 "
        "상태를 확인하고, 서비스가 복구됐으면 “작업 다시 시도”를 눌러 주세요."
    )


@pytest.mark.parametrize(
    "code",
    [
        # 예전 부분 문자열 규칙이 검수 라벨로 끌어가던, 검수가 아닌 코드들
        "SOURCE_PROVIDER_UNAVAILABLE",
        "ESSENCE_PROVIDER_UNAVAILABLE",
        "REVIEW_UNAVAILABLE",
        "BROKER_UNAVAILABLE",
        "SOMETHING_UNCERTAIN",
    ],
)
def test_the_review_label_is_matched_exactly_not_by_substring(code):
    assert blocker_copy(code).title != _REVIEW_PENDING
