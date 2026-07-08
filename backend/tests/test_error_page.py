import pytest

from app.utils.error_page import looks_like_error_page_text


@pytest.mark.parametrize(
    "text",
    [
        # 실증 버그의 잔재 형태.
        "Title: 403 Forbidden",
        "자료에서 확인된 핵심 메시지: Title: 403 Forbidden",
        # Jina Reader 폴백 전형 출력.
        "Title: 403 Forbidden\n\nURL Source: https://example.com\n\nMarkdown Content:\n",
        "Title: 404 Not Found\n본문 없음",
        "Title: Access Denied",
        "Title: Attention Required! | Cloudflare",
        "Title: 접근이 거부되었습니다",
        "Title: 요청이 거부되었습니다 (차단)",
        # HTTP 상태코드 + 오류 단어(영/한).
        "Title: 500 Internal Server Error",
        "Title: 502 오류",
    ],
)
def test_looks_like_error_page_text_flags_error_remnants(text):
    assert looks_like_error_page_text(text) is True


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        # 무해한 Title 라인은 오탐하지 않는다.
        "Title: 병원 소개",
        "Title: 원장 인사말",
        # 숫자 코드만 담긴 정상 제목("500가지")은 오류로 보지 않는다.
        "Title: 500가지 건강 상식",
        "Title: 404가지 다이어트 팁",
        # 오류 단어가 있어도 Title 세그먼트 밖의 일반 본문은 판정 대상이 아니다.
        "충분한 설명으로 환자의 접근 권한을 존중하는 진료를 지향합니다.",
        "원장님은 치료 전 과정을 차분히 설명합니다.",
    ],
)
def test_looks_like_error_page_text_ignores_safe_text(text):
    assert looks_like_error_page_text(text) is False
