"""규칙 대조(`mention_identity`) — 2026-11-09 감사에서 옛 판정이 틀린 유형을 고정한다.

사례 문장은 실제 AI 답변의 형태(굵은 이름, 목록, 하위 '위치:' 줄, 표)를 따라 만든 것이다.
"""

import pytest

from app.services import mention_identity as mi


def _identity(name, *, address=None, phone=None, region=()):
    return mi.MentionIdentity.build(name, address=address, phone=phone, region_terms=region)


MAPO = _identity(
    "마포성모탑정형외과",
    address="서울 마포구 큰우물로 76 고려빌딩 1층 2호",
    phone="02-711-8538",
    region=("마포구", "도화동", "마포역"),
)
SINGI = _identity(
    "신기한속내과연합의원",
    address="대구광역시 동구 율하동로8길 73",
    region=("대구 동구", "율하동", "안심", "신기역"),
)
SEOUL_W = _identity(
    "서울W내과의원 위례점",
    address="경기도 하남시 위례대로 256, 3층 301호 (학암동)",
    phone="02-6941-1110",
    region=("하남시", "위례", "학암동", "성남 수정구"),
)
NOWON = _identity("노원탑365의원", address="서울 노원구 동일로 1414", region=("노원구", "상계동", "노원역"))
NEWPLAN = _identity(
    "뉴플랜여성의원",
    address="부산광역시 남구 유엔평화로 4",
    region=("부산 남구", "대연동", "대연역"),
)
YONSEI = _identity(
    "연세속시원내과의원", address="경상북도 경산시 성동로 2 (정평동)", region=("경산", "정평동")
)
JANG = _identity(
    "장편한외과의원",
    address="경기 수원시 팔달구 중부대로 247",
    phone="031-8067-8114",
    region=("수원시 팔달구",),
)


@pytest.mark.parametrize(
    ("identity", "answer", "expected_text"),
    [
        # 공식 등록명 — 이름 앞 지역명이 빠졌다(옛 판정은 이 병원의 9월 이후 언급 129건을 모두 0으로 셌다).
        (MAPO, "3. **성모탑정형외과의원**\n   - 주소: 서울 마포구 큰우물로 76", "성모탑정형외과의원"),
        (MAPO, "* **성모탑정형외과의원 (마포역 2번 출구 인근)**: 관절 클리닉", "성모탑정형외과의원"),
        # '연합'이 빠진 표기·중간에 끼운 표기.
        (SINGI, "* **신기한속내과의원** (동구 안심로 / 신기역 인근)", "신기한속내과의원"),
        (YONSEI, "* **연세속시원연합내과의원**\n  * **위치**: 경산시 대학로 73 (정평동)", "연세속시원연합내과의원"),
        # 로마자 ↔ 한글 표기.
        (SEOUL_W, "### 3. **서울더블유(W)내과의원 위례점** (하남시 학암동)", "서울더블유(W)내과의원"),
        # 진료과 단어를 뗀 브랜드명 — 같은 항목에 위치 증거가 있으면 인정한다.
        (NEWPLAN, "* **뉴플랜의원**\n  * **위치**: 부산 남구 유엔평화로 4 (대연동)", "뉴플랜의원"),
        # 조사가 붙은 산문 속 언급.
        (JANG, "수원 팔달구라면 우만동 장편한외과 등이 대표적입니다.", "장편한외과"),
        (JANG, "광교 인근이라면 장편한외과의원을 추천합니다.", "장편한외과의원"),
    ],
)
def test_real_mentions_are_matched(identity, answer, expected_text):
    resolution = mi.resolve(identity, answer)

    assert resolution.matched
    assert resolution.matched_text == expected_text


def test_phone_number_identifies_the_clinic_even_without_the_name():
    resolution = mi.resolve(SEOUL_W, "위례 지역 검진: 위례대로 256 3층, 문의 02-6941-1110")

    assert resolution.matched
    assert resolution.evidence == mi.EVIDENCE_PHONE


@pytest.mark.parametrize(
    ("identity", "answer"),
    [
        # 더 긴 다른 병원 이름의 일부.
        (NOWON, "### 2. 모두탑365정형외과 노원점 — 상계동·노원역 인근"),
        (SINGI, "**경대연합시원한속내과의원** - 주소: 대구 동구 경안로 791"),
        # 다른 지점.
        (SEOUL_W, "* **서울W내과 성남점** (성남시 중원구)"),
        # 같은 이름이지만 다른 시·군·구.
        (JANG, "* **장편한외과의원** (부산 해운대구 좌동)"),
        # 이름 조각만 겹치는 평범한 문장.
        (_identity("행복한의원", region=("수서역",)), "행복한 진료를 위해 노력합니다."),
        # 이름이 없다.
        (MAPO, "마포구에는 정형외과가 많습니다. 마포바로본정형외과의원을 참고하세요."),
    ],
)
def test_other_clinics_and_fragments_are_not_matched(identity, answer):
    assert not mi.resolve(identity, answer).matched


def test_brand_without_location_is_left_for_a_second_look():
    resolution = mi.resolve(NEWPLAN, "여성 질환은 **뉴플랜의원**도 선택지입니다.")

    assert resolution.status == mi.BRAND_UNCONFIRMED
    assert mi.needs_second_look(NEWPLAN, "여성 질환은 **뉴플랜의원**도 선택지입니다.", resolution)


def test_matches_need_no_second_look_and_unrelated_answers_need_none_either():
    matched = "**성모탑정형외과의원** (마포역 인근)"
    unrelated = "정형외과는 가까운 곳이 좋습니다."

    assert not mi.needs_second_look(MAPO, matched, mi.resolve(MAPO, matched))
    assert not mi.needs_second_look(MAPO, unrelated, mi.resolve(MAPO, unrelated))


def test_other_location_with_the_same_name_gets_a_second_look():
    answer = "* **장편한외과의원** (부산 해운대구 좌동)"

    resolution = mi.resolve(JANG, answer)

    assert resolution.status == mi.OTHER_LOCATION
    assert mi.needs_second_look(JANG, answer, resolution)


def test_numbered_list_items_do_not_borrow_the_previous_items_location():
    home = _identity("우리의원", region=("수원시 팔달구",))
    answer = "1. **경쟁병원** (팔달구)\n2. **멀리내과의원** (부산 해운대구)"

    assert not mi.resolve(home.for_competitor("멀리내과의원"), answer).matched
    assert mi.resolve(home.for_competitor("경쟁병원"), answer).matched


def test_short_names_are_matched_with_their_suffix_only():
    gana = _identity("가나의원")

    assert mi.resolve(gana, "가나의원과 다라병원을 추천합니다.").matched
    assert not mi.resolve(gana, "가나다라 순으로 정리했습니다.").matched


def test_words_ending_like_places_are_not_read_as_another_district():
    answer = "* **강심장내과의원** — 대사증후군·고혈압 진료, 마산역 출구 근처"
    identity = _identity("강심장내과의원", region=("창원시 마산회원구",))

    assert mi.resolve(identity, answer).matched


def test_identity_material_changes_with_every_judgment_input():
    base = MAPO.material()

    assert base["resolver"] == mi.RESOLVER_VERSION
    assert base["phone"] == "027118538"
    assert base != _identity("마포성모탑정형외과", address=MAPO.address, phone=None, region=MAPO.region_terms).material()
