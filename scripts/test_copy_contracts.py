"""백엔드와 프런트엔드가 각자 들고 있는 **같은 값**이 갈라지지 않게 잡는 가드.

런타임을 공유하지 않는 두 언어 사이에는 단일 소스가 있을 수 없다. 그래서 값은 양쪽이
각각 선언하되, 그 두 선언이 같은지는 CI가 소스 텍스트를 읽어 확인한다. 각 언어의
자기 테스트가 자기 상수를 리터럴과 비교하는 것으로는 드리프트를 절대 못 잡는다 —
한쪽을 바꾸면서 같은 파일의 리터럴도 같이 고치면 그 테스트는 계속 초록이다.

stdlib만 쓴다: `pytest scripts` 레인은 백엔드 의존성 설치 없이도 돌아야 한다.
"""

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_BACKEND_DIAGNOSIS = PROJECT_ROOT / "backend" / "app" / "api" / "public" / "diagnosis.py"
_SITE_DIAGNOSIS_SLOTS = PROJECT_ROOT / "site" / "lib" / "diagnosis-slots.ts"
_BACKEND_ESSENCE_SOURCES = PROJECT_ROOT / "backend" / "app" / "services" / "essence_sources.py"
_ADMIN_ESSENCE_SOURCE_SPLIT = PROJECT_ROOT / "admin" / "lib" / "essence-source-split.ts"


def _read_int_constant(path: Path, pattern: str) -> int:
    text = path.read_text(encoding="utf-8")
    matches = re.findall(pattern, text, re.MULTILINE)
    assert len(matches) == 1, (
        f"{path.relative_to(PROJECT_ROOT)}에서 {pattern!r}로 상수를 정확히 하나 찾지 못했다 "
        f"(찾은 값: {matches}). 선언이 바뀌었으면 이 파서를 함께 고쳐야 한다 — "
        "파서가 조용히 0건을 반환하면 가드가 통과하면서 아무것도 지키지 않게 된다."
    )
    return int(matches[0])


def test_diagnosis_slot_reset_hour_matches_across_backend_and_site() -> None:
    """무료 진단 자리 리셋 시각은 백엔드 배정 로직과 화면 안내 문구가 같아야 한다.

    어긋나면 신청자가 "매일 오전 N시에 새 접수가 열립니다"를 보고 그 시각에 왔는데
    아직 마감 화면이거나, 반대로 이미 자리가 열렸는데 안 열린 줄 알고 돌아간다.
    """
    backend_hour = _read_int_constant(
        _BACKEND_DIAGNOSIS, r"^SLOT_RESET_HOUR_KST\s*=\s*(\d+)\s*$"
    )
    site_hour = _read_int_constant(
        _SITE_DIAGNOSIS_SLOTS,
        r"^export const DIAGNOSIS_SLOT_RESET_HOUR_KST\s*=\s*(\d+)\s*$",
    )

    assert backend_hour == site_hour, (
        f"자리 리셋 시각이 갈라졌다: backend/app/api/public/diagnosis.py="
        f"{backend_hour}, site/lib/diagnosis-slots.ts={site_hour}. "
        "둘 다 같은 값으로 맞출 것 — 화면 안내 문구와 실제 배정 경계가 어긋난다."
    )


def _single_match(path: Path, pattern: str, flags: int = re.MULTILINE) -> str:
    text = path.read_text(encoding="utf-8")
    matches = re.findall(pattern, text, flags)
    assert len(matches) == 1, (
        f"{path.relative_to(PROJECT_ROOT)}에서 {pattern!r}로 선언을 정확히 하나 찾지 못했다 "
        f"(찾은 개수: {len(matches)}). 선언 형태가 바뀌었으면 이 파서를 함께 고쳐야 한다 — "
        "파서가 조용히 0건을 반환하면 가드가 통과하면서 아무것도 지키지 않게 된다."
    )
    return matches[0]


def _backend_blank_chars() -> set[str]:
    """`_BLANK_CHARS = (...)` 우변을 그대로 평가한다 (암묵적 문자열 연결 포함)."""
    literal = _single_match(
        _BACKEND_ESSENCE_SOURCES,
        r"^_BLANK_CHARS\s*=\s*(\(.*?\))\s*$",
        re.MULTILINE | re.DOTALL,
    )
    return set(ast.literal_eval(literal))


def _admin_blank_chars() -> set[str]:
    """`BLANK_TEXT_RE = /^[...]*$/`의 문자 클래스를 코드포인트 집합으로 푼다."""
    char_class = _single_match(
        _ADMIN_ESSENCE_SOURCE_SPLIT,
        r"^const BLANK_TEXT_RE\s*=\s*/\^\[(.*?)\]\*\$/\s*$",
    )

    # (코드포인트, 이스케이프 여부). `\uXXXX` 외의 백슬래시 표기를 뒤 문자로 눙치면
    # `\t`가 문자 `t`로 둔갑해 가드가 엉뚱한 집합을 비교하고도 초록이 된다 — 그 자리에서 실패시킨다.
    tokens: list[tuple[str, bool]] = []
    index = 0
    while index < len(char_class):
        character = char_class[index]
        if character == "\\":
            escape = char_class[index : index + 6]
            if re.fullmatch(r"\\u[0-9A-Fa-f]{4}", escape) is None:
                raise ValueError(
                    f"BLANK_TEXT_RE에 이 파서가 모르는 이스케이프가 있다: "
                    f"{char_class[index : index + 2]!r}. \\uXXXX만 읽는다 — 표기가 늘었으면 "
                    "이 파서를 함께 고쳐야 한다."
                )
            tokens.append((chr(int(escape[2:], 16)), True))
            index += 6
        else:
            tokens.append((character, False))
            index += 1

    chars: set[str] = set()
    position = 0
    while position < len(tokens):
        character, escaped = tokens[position]
        # 범위(`a-z`)는 **이스케이프되지 않은** `-`가 양쪽 토큰 사이에 있을 때만이다.
        # 위 토큰화가 `\uXXXX` 외의 이스케이프를 전부 거부하므로 `\-`는 여기까지 오지
        # 못한다(그 자리에서 ValueError). 양끝의 `-`는 리터럴이다.
        if character == "-" and not escaped and 0 < position < len(tokens) - 1:
            for code_point in range(ord(tokens[position - 1][0]), ord(tokens[position + 1][0]) + 1):
                chars.add(chr(code_point))
            position += 2
        else:
            chars.add(character)
            position += 1
    return chars


def test_blank_text_set_matches_across_backend_and_admin() -> None:
    """원문이 비어 있다는 판정의 공백 집합은 백엔드와 admin 화면이 같아야 한다.

    어긋나면 NBSP·전각 공백만 든 자료를 두고 한쪽은 "원문 있음", 다른 쪽은 "없음"으로
    세어 필수 자료 분모가 갈라진다. 화면은 "처리 완료 12/12"인데 서버는 승인 게이트를
    열어 주지 않고, AE는 무엇이 남았는지 볼 방법이 없다.
    """
    backend_chars = _backend_blank_chars()
    admin_chars = _admin_blank_chars()

    assert backend_chars == admin_chars, (
        "공백 집합이 갈라졌다: "
        f"backend에만 있음={sorted(hex(ord(c)) for c in backend_chars - admin_chars)}, "
        f"admin에만 있음={sorted(hex(ord(c)) for c in admin_chars - backend_chars)}. "
        "backend/app/services/essence_sources.py의 _BLANK_CHARS와 "
        "admin/lib/essence-source-split.ts의 BLANK_TEXT_RE를 같은 집합으로 맞출 것."
    )
