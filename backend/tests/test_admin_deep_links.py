"""백엔드가 만드는 Admin 딥링크가 옛 병원 화면 경로를 가리키지 않는지 (2026-10-09 제거).

병원 화면은 `/hospitals/{id}` 아래 탭 4개(현황 · 병원 정보 · 콘텐츠 · 보고서)뿐이다.
옛 8개 경로는 `admin/lib/route-redirects.ts`가 redirect만 해 주고 2026-10-09에 사라진다.
그 뒤에도 인시던트·Slack·운영 센터 버튼이 옛 경로를 만들면 운영자가 누르는 링크가 404가
된다. API 경로(`/admin/hospitals/...`, `/api/admin/hospitals/...`)는 대상이 아니다.
"""

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_RETIRED = (
    "dashboard",
    "onboarding",
    "profile",
    "schedule",
    "wiki",
    "essence",
    "query-targets",
    "exposure-actions",
)
# 따옴표 바로 뒤에서 시작하는 화면 경로만 본다 — `/admin/hospitals/...`는 API다.
_SCREEN_LINK = re.compile(
    r"""["'](?:\{[^}]*ADMIN_BASE_URL[^}]*\})?/hospitals/\{[^}]+\}/(%s)(?=["'/?#])"""
    % "|".join(re.escape(segment) for segment in _RETIRED)
)


def test_backend_admin_links_use_the_four_hospital_tabs():
    offenders = []
    for path in _APP.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SCREEN_LINK.search(line):
                offenders.append(f"{path.relative_to(_APP.parent)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_the_scanner_still_sees_a_retired_link():
    """정규식이 조용히 아무것도 못 잡게 바뀌면 위 검사가 무력해진다."""
    assert _SCREEN_LINK.search('admin_path=f"/hospitals/{hospital.id}/onboarding",')
    assert _SCREEN_LINK.search(
        "f\"{settings.ADMIN_BASE_URL.rstrip('/')}/hospitals/{context.hospital_id}/schedule\""
    )
    assert not _SCREEN_LINK.search('"/admin/hospitals/{hospital_id}/schedule"')
    assert not _SCREEN_LINK.search('admin_path=f"/hospitals/{hospital.id}/info",')


def test_incidents_stored_with_a_retired_path_open_the_current_tab():
    """2026-10-09 전에 저장된 인시던트는 다시 touch되기 전까지 옛 경로를 들고 있다."""
    from app.services.incident_safety import current_admin_path, normalize_admin_path

    hospital = "0f8b9c1e-0000-0000-0000-000000000001"
    assert normalize_admin_path(f"/hospitals/{hospital}/onboarding") == f"/hospitals/{hospital}/info"
    assert normalize_admin_path(f"/hospitals/{hospital}/schedule") == f"/hospitals/{hospital}/content"
    assert normalize_admin_path(f"/hospitals/{hospital}/dashboard") == f"/hospitals/{hospital}"
    # 새 탭과 그 밖의 경로는 건드리지 않는다.
    for path in (f"/hospitals/{hospital}/info", f"/hospitals/{hospital}/reports", "/operations"):
        assert current_admin_path(path) == path


def test_the_backend_mapping_matches_the_admin_redirects():
    """두 목록이 어긋나면 Admin redirect와 백엔드 링크가 서로 다른 탭을 연다."""
    from app.services.incident_safety import _RETIRED_HOSPITAL_TABS

    source = (_APP.parents[1] / "admin" / "lib" / "route-redirects.ts").read_text(encoding="utf-8")
    targets = dict(
        re.findall(r"""^\s*'?([a-z-]+)'?: \{ path: '([a-z]*)'""", source, re.MULTILINE)
    )
    assert set(targets) == set(_RETIRED) == set(_RETIRED_HOSPITAL_TABS)
    for segment, tab in targets.items():
        assert _RETIRED_HOSPITAL_TABS[segment] == (f"/{tab}" if tab else "")
