"""백엔드와 프런트엔드가 각자 들고 있는 **같은 값**이 갈라지지 않게 잡는 가드.

런타임을 공유하지 않는 두 언어 사이에는 단일 소스가 있을 수 없다. 그래서 값은 양쪽이
각각 선언하되, 그 두 선언이 같은지는 CI가 소스 텍스트를 읽어 확인한다. 각 언어의
자기 테스트가 자기 상수를 리터럴과 비교하는 것으로는 드리프트를 절대 못 잡는다 —
한쪽을 바꾸면서 같은 파일의 리터럴도 같이 고치면 그 테스트는 계속 초록이다.

stdlib만 쓴다: `pytest scripts` 레인은 백엔드 의존성 설치 없이도 돌아야 한다.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_BACKEND_DIAGNOSIS = PROJECT_ROOT / "backend" / "app" / "api" / "public" / "diagnosis.py"
_SITE_DIAGNOSIS_SLOTS = PROJECT_ROOT / "site" / "lib" / "diagnosis-slots.ts"


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
