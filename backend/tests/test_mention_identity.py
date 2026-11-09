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
    answer = "1. **장편한외과의원** (부산 해운대구)\n2. **다른내과의원** (수원시 팔달구)"

    # 우리 병원 판정은 옆 항목의 위치를 빌려 오지 않는다.
    assert not mi.resolve(JANG, answer).matched


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


# ── 2026-11-09 Codex 검토에서 나온 경계 사례 ──


def test_same_city_but_another_district_is_not_ours():
    answer = "* **장편한외과의원** (수원시 영통구 하동)"

    resolution = mi.resolve(JANG, answer)

    assert resolution.status == mi.OTHER_LOCATION
    assert not mi.resolve(MAPO, "* **성모탑정형외과의원** (서울 강남구 역삼동)").matched


@pytest.mark.parametrize(
    "answer",
    [
        "* **서울W내과의원** 성남점 (성남시 중원구)",
        "서울W내과의원(성남점)은 검진이 가능합니다.",
        "서울W내과의원 성남점은 검진이 가능합니다.",
    ],
)
def test_another_branch_after_markdown_parentheses_or_particles_is_not_ours(answer):
    assert not mi.resolve(SEOUL_W, answer).matched


def test_own_branch_in_parentheses_is_ours():
    assert mi.resolve(SEOUL_W, "서울W내과의원(위례점)은 하남시 학암동에 있습니다.").matched


def test_plain_bullets_do_not_borrow_the_next_items_location():
    answer = "- 장편한외과의원 (부산 해운대구)\n- 다른내과의원 (수원시 팔달구)"

    assert not mi.resolve(JANG, answer).matched


def test_sub_bullets_under_a_heading_still_belong_to_the_item():
    answer = "### 1. **장편한외과의원** (광교/우만동 인근)\n* **위치:** 경기도 수원시 팔달구 중부대로 247"

    resolution = mi.resolve(JANG, answer)

    assert resolution.matched and resolution.evidence == mi.EVIDENCE_ROAD


def test_phone_with_another_area_code_is_not_ours():
    assert not mi.resolve(JANG, "부산 다른내과의원 연락처는 051-8067-8114입니다.").matched
    assert mi.resolve(JANG, "문의: +82-31-8067-8114").matched


def test_hangul_registered_name_matches_the_latin_spelling():
    identity = _identity("서울더블유내과의원", region=("하남시", "위례"))

    assert mi.resolve(identity, "**서울W내과의원** (하남시 학암동)").matched
    assert "울w내" in mi.name_fragments(identity)


def test_frozen_identity_material_round_trips():
    restored = mi.MentionIdentity.from_material(SEOUL_W.material())

    assert restored is not None
    assert restored.material() == SEOUL_W.material()
    assert mi.MentionIdentity.from_material({"name": ""}) is None


# ── 2026-11-09 Codex 2차 검토 사례 ──


def test_location_on_the_previous_line_of_the_same_paragraph_counts():
    assert mi.resolve(JANG, "부산 해운대구 우동에 위치한\n장편한외과의원을 추천합니다.").status == mi.OTHER_LOCATION


def test_parent_bullet_location_counts():
    answer = "* 부산 해운대구\n  * 장편한외과의원 — 치질 수술"

    assert mi.resolve(JANG, answer).status == mi.OTHER_LOCATION


def test_residents_of_another_district_do_not_move_the_clinic():
    answer = "성모탑정형외과의원은 마포역 인근으로 용산구 주민도 이용하기 편리합니다."

    assert mi.resolve(MAPO, answer).matched


@pytest.mark.parametrize(
    "answer",
    [
        "서울W내과의원 성남점에서는 검진이 가능합니다.",
        "서울W내과의원은 성남점에서 검진을 합니다.",
    ],
)
def test_compound_particles_do_not_hide_another_branch(answer):
    assert not mi.resolve(SEOUL_W, answer).matched


def test_generic_branch_word_is_not_another_branch():
    assert mi.resolve(SEOUL_W, "**서울W내과의원** 지점은 위례(하남시 학암동)에 있습니다.").matched


def test_daegu_is_a_city_not_a_district():
    assert not mi.resolve(SINGI, "* **신기한속내과의원** (대구 북구 복현동)").matched
    assert mi.resolve(SINGI, "* **신기한속내과의원** (대구 동구 신기역 인근)").matched


def test_metropolitan_city_names_are_places():
    assert not mi.resolve(SINGI, "* **신기한속내과의원** (부산광역시 동구 초량동)").matched


def test_multi_letter_latin_spelling_of_a_hangul_registered_name():
    identity = _identity("에스케이정형외과의원", region=("수원시 팔달구",))

    assert mi.resolve(identity, "* **SK정형외과의원** (수원시 팔달구)").matched


# ── 2026-11-09 Codex 3차 검토 사례 ──


def test_a_new_heading_does_not_borrow_the_previous_items_location():
    answer = "### 1. 다른외과의원\n* 위치: 수원시 팔달구\n### 2. 장편한외과의원 (부산 해운대구)"

    assert mi.resolve(JANG, answer).status == mi.OTHER_LOCATION


def test_superlatives_after_a_place_are_still_the_clinics_location():
    answer = "부산 해운대구에서 가장 유명한 장편한외과의원을 추천합니다."

    assert mi.resolve(JANG, answer).status == mi.OTHER_LOCATION


def test_prose_after_a_list_does_not_borrow_the_lists_location():
    answer = "* 다른외과의원 (수원시 팔달구)\n장편한외과의원은 부산 해운대구에 있습니다."

    assert mi.resolve(JANG, answer).status == mi.OTHER_LOCATION


# ── 2026-11-09 경쟁 병원 재판정에서 나온 사례(v2) ──

HOME_SEJONG = _identity("세종다온신경과의원", region=("세종시", "나성동", "보람동"))
HOME_BUSAN = _identity("뉴플랜여성의원", address="부산광역시 남구 유엔평화로 4", region=("부산 남구", "광안동"))


def test_competitor_written_with_a_locality_prefix_is_matched():
    answer = "* **세종두미소신경과의원** (세종 보람동/나성동 인근)"

    assert mi.resolve(HOME_SEJONG.for_competitor("두미소신경과의원"), answer).matched


def test_branch_registered_without_jeom_is_matched_with_or_without_it():
    identity = HOME_SEJONG.for_competitor("화인마취통증의학과의원 세종")

    assert mi.resolve(identity, "* **화인마취통증의학과의원 세종점** — 나성동").matched
    assert mi.resolve(identity, "화인마취통증의학과의원 세종에서 상담하세요.").matched
    assert not mi.resolve(identity, "화인마취통증의학과의원 대전점이 있습니다.").matched


def test_competitors_in_a_neighbouring_district_are_still_mentions():
    answer = "* **광안자모병원** (수영구 광안동)\n  * 여성성형 클리닉 운영"

    assert mi.resolve(HOME_BUSAN.for_competitor("광안자모병원"), answer).matched


def test_locality_prefix_does_not_open_longer_unrelated_names():
    # '세종'이 앞에 붙은 것은 받지만, 다른 글자가 붙은 더 긴 이름은 여전히 다른 병원이다.
    assert not mi.resolve(HOME_SEJONG.for_competitor("두미소신경과의원"), "* **새두미소신경과의원**").matched


def test_city_prefix_is_only_for_competitors_and_city_level_names():
    # 우리 병원은 앞에 지명이 붙은 더 긴 이름을 받지 않는다.
    assert not mi.resolve(_identity("바른내과의원", region=("마포구",)), "* **마포바른내과의원** (마포구)").matched
    # 경쟁 병원도 동·역·구 이름 접두어는 받지 않는다.
    home = _identity("우리의원", region=("대구 동구", "신기역", "마포구"))
    assert not mi.resolve(home.for_competitor("연세내과의원"), "* **신기연세내과의원**").matched
    assert not mi.resolve(home.for_competitor("바른내과의원"), "* **마포바른내과의원**").matched


def test_bare_branch_rejects_another_city_and_ignores_non_place_words():
    identity = HOME_SEJONG.for_competitor("화인마취통증의학과의원 세종")
    assert not mi.resolve(identity, "* **화인마취통증의학과의원 대전** — 둔산동").matched
    # 시 단위가 아닌 끝 낱말은 지점이 아니라 이름의 일부다.
    assert not mi.resolve(HOME_SEJONG.for_competitor("프라임의원 메디컬"), "프라임의원을 추천합니다.").matched


def test_competitor_in_another_metro_is_another_place_but_names_and_origins_are_not():
    gyeonggi = _identity("우리의원", region=("경기도 하남시", "위례"))
    # 이름 자체의 '서울'은 위치가 아니다.
    assert mi.resolve(gyeonggi.for_competitor("서울W내과의원 위례점"), "* **서울W내과의원 위례점** (하남시)").matched
    # 우리 시·도를 모르면 우리 생활권 말이 있는 한 다른 곳이 아니다.
    suwon = _identity("우리의원", region=("수원시 팔달구",))
    assert mi.resolve(suwon.for_competitor("경쟁내과의원"), "* **경쟁내과의원** (경기도 수원시 장안구)").matched
    assert not mi.resolve(suwon.for_competitor("멀리내과의원"), "* **멀리내과의원** (부산 해운대구)").matched
    # 광주 광역시 구와 조사가 붙은 시·도.
    daegu = _identity("우리의원", region=("대구 동구",))
    assert not mi.resolve(daegu.for_competitor("멀리내과의원"), "* **멀리내과의원** — 광주 북구").matched
    assert not mi.resolve(daegu.for_competitor("멀리내과의원"), "* **멀리내과의원**: 부산에서만 진료").matched
    # 환자가 오는 곳은 병원 위치가 아니다.
    assert mi.resolve(daegu.for_competitor("가까운내과의원"), "* **가까운내과의원** — 서울에서 KTX로 오시는 분도 많습니다.").matched


def test_competitor_spelling_differences_marketers_register():
    # 등록은 '김외과의원', 답변은 생활권 시 이름을 붙인 '수원김외과의원'.
    assert mi.resolve(JANG.for_competitor("김외과의원"), "* **수원김외과의원**\n  * **위치:** 경기도 수원시 팔달구").matched
    # '마취통증의학과' ↔ '통증의학과'.
    assert mi.resolve(
        HOME_SEJONG.for_competitor("화인마취통증의학과의원 세종"), "* **화인통증의학과의원 세종점** (나성동)"
    ).matched
    # '내과' ↔ '속내과'.
    daegu = _identity("우리내과의원", region=("대구 수성구", "신매동"))
    assert mi.resolve(daegu.for_competitor("서울아산내과의원"), "### 2. 서울아산속내과의원\n* **위치**: 대구광역시 수성구").matched
    # 우리 병원 판정에는 쓰지 않는다.
    assert not mi.resolve(_identity("서울아산내과의원", region=("대구 수성구",)), "서울아산속내과의원 (수성구)").matched
