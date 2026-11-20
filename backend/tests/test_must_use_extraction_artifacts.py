"""필수 문구에 깨진 추출 문장·자료 머리말·환자 요청·메모체가 들어가지 않는다(2026-11-20).

운영 확인(읽기 전용): 자동 판 다올연합·365서귀포·장이편한·다대으뜸·유내과가 PDF 추출로 글자 사이가
띄어진 문장을, 뉴플랜이 자료 머리말과 환자 요청 문장을 필수 문구로 골랐다. 옛 승인본 행복드림 v9는
메모체(…필요함) 문구를 원문 그대로 요구했다. 모두 공개 글에 그대로 들어갈 수 있었다.
"""

from types import SimpleNamespace

import pytest

from app.services.essence_claims import (
    AUTOMATIC_ESSENCE_POLICY_VERSION,
    has_broken_spacing,
    is_not_doctor_statement,
)
from app.services.must_use_verbatim import (
    EXCLUDED_NOT_DOCTOR_STATEMENT,
    must_use_requirement,
)

BROKEN = [
    "검 사 장비 가 많 아서 검 사 를 많 이 합니 다 .",
    "질 병 을 정 복 하 겠 다 명의 가 되 겠 다 등 의 거 창 한 이 유 가 있 었던 건 아 닙 니 다 .",
    "경과를 선 택 하 게 되 었 습니 다 .",
    "바 쁜 일상으 로 인 한 시 간 적 부 담 을 덜 어드 립 니 다.",
    "내 시 경 검 진 환 자를 더 늘 리고 싶 습니 다 .",
    "부터 소화 기 내 시 경 , 비만 · 대 사 관 리 까 지 폭 넓 은 영 역 에서 환 자 분 들의 건강 한 내일을 설계 하 는 동 반 자 가 되 고 자 합니 다",
    "내 시 경 10000 례 이 상의 경 력 이 있습니 다",
]

HEADERS_AND_REQUESTS = [
    "원장님 요청에 따라 수술 건수, 경력 연차, 일부 표현을 제외한 편집본입니다.",
    "임신 초기에 영양제 추천해주세요",
]

MEMO_STYLE = [
    "생후 6개월 미만의 영아는 38도 이상 열이 나면 '며칠 더 지켜보자'고 말씀드리지 않고 즉시 상태 확인이 필요함",
    "구토 후 위를 쉬게 하는 20~30분 대기, 그 후 소량의 경구수액으로 시작하는 단계적 회복 순서를 지켜야 함",
]

CLEAN = [
    "꼭 필요한 치료만 정직하게 하겠습니다.",
    "한 분 한 분 정성껏 상담해드리겠습니다.",
    "진료할 때는 그림을 보여 드리거나 검사 결과지에 밑줄을 그어 가며 이해하기 쉽게 설명합니다.",
    "저는 무턱대고 수액을 권하기보다 지금 몸에서 무엇이 부족한지부터 꼼꼼히 확인하는 편입니다.",
    "아이가 소변을 잘 보고 있는지, 입술과 입안이 촉촉한지, 평소 활동량에 변화가 있는지 함께 확인해주세요.",
    "빠른 내시경이 아니라 바른 내시경 검사를 지향하며, 매주 회의를 통해 진료 과정을 점검합니다.",
]


@pytest.mark.parametrize("sentence", BROKEN)
def test_broken_extraction_spacing_is_not_a_doctor_statement(sentence: str) -> None:
    assert has_broken_spacing(sentence)
    assert is_not_doctor_statement(sentence)


@pytest.mark.parametrize("sentence", HEADERS_AND_REQUESTS + MEMO_STYLE)
def test_headers_requests_and_memo_style_are_not_doctor_statements(sentence: str) -> None:
    assert is_not_doctor_statement(sentence)


@pytest.mark.parametrize("sentence", CLEAN)
def test_clean_doctor_sentences_still_pass(sentence: str) -> None:
    assert not has_broken_spacing(sentence)
    assert not is_not_doctor_statement(sentence)


def _approved(messages: list[str], metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id="p1",
        version=9,
        status="APPROVED",
        is_base=True,
        must_use_messages=messages,
        synthesis_metadata=metadata or {},
    )


def test_legacy_approval_drops_memo_style_and_keeps_clean_doctor_sentences() -> None:
    messages = CLEAN[:2] + MEMO_STYLE
    requirement = must_use_requirement(_approved(messages))
    assert list(requirement.messages) == CLEAN[:2]
    assert [item.reason for item in requirement.excluded] == [EXCLUDED_NOT_DOCTOR_STATEMENT] * 2
    assert [item.index for item in requirement.excluded] == [2, 3]


def test_automatic_version_of_the_current_policy_drops_broken_spacing() -> None:
    texts = BROKEN[:2] + [CLEAN[0]]
    metadata = {
        "mode": "AUTOMATIC",
        "policy_version": AUTOMATIC_ESSENCE_POLICY_VERSION,
        "must_use": {"mode": "SOURCE_VERBATIM", "items": [{"text": text} for text in texts]},
    }
    requirement = must_use_requirement(_approved(texts, metadata))
    assert list(requirement.messages) == [CLEAN[0]]
