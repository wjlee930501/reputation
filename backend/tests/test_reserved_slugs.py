"""예약 경로 동기화 (PRD F1-3).

공개 표면(`site`)의 예약 접두어와 백엔드의 예약 slug가 어긋나면 **조용히** 깨진다:

- site에 `/ai-diagnosis`가 없으면 커스텀 도메인에서 `/{slug}/ai-diagnosis`로 rewrite돼
  무료 진단 퍼널이 병원 페이지 밑으로 사라진다.
- 백엔드에 없으면 slug가 `ai-diagnosis`인 병원이 만들어지고, 그 병원 페이지가
  퍼널에 먹혀 영원히 열리지 않는다.

어느 쪽도 에러를 내지 않으므로 테스트가 없으면 배포 후에야 안다.
목록을 여기 복사하지 않고 **양쪽 소스에서 읽어 비교**한다 — 복사하면 셋을 같이
고치는 한 통과해서 드리프트를 못 잡는다.
"""
import re
from pathlib import Path

from app.api.admin.hospitals import RESERVED_SITE_SLUGS

_HOST_ROUTING = (
    Path(__file__).resolve().parents[2] / "site" / "lib" / "host-routing.ts"
)


def _site_reserved_prefixes() -> set[str]:
    text = _HOST_ROUTING.read_text(encoding="utf-8")
    match = re.search(r"const RESERVED_PREFIXES = \[(.*?)\]", text, re.DOTALL)
    assert match, f"host-routing.ts에서 RESERVED_PREFIXES를 찾지 못했다: {_HOST_ROUTING}"
    return {value.strip("/") for value in re.findall(r"'([^']+)'", match.group(1))}


def test_site_reserved_prefixes_are_parsed():
    """파싱이 깨져 빈 집합이 되면 아래 검사가 조용히 무력해진다."""
    prefixes = _site_reserved_prefixes()
    assert "api" in prefixes
    assert len(prefixes) >= 4


def test_the_diagnosis_funnel_path_is_reserved_on_both_sides():
    """이 경로 하나가 리드마그넷 전체의 입구다."""
    assert "ai-diagnosis" in _site_reserved_prefixes()
    assert "ai-diagnosis" in RESERVED_SITE_SLUGS


def test_backend_reserves_every_path_the_site_reserves():
    """site가 가로채는 경로는 병원 slug로 발급되면 안 된다.

    반대 방향(백엔드가 더 많이 예약)은 허용한다 — 안전한 쪽으로 더 막는 것이므로.
    `_next`는 Next.js 내부 경로라 slug 패턴상 만들어질 수 없어 제외한다.
    """
    site_only = _site_reserved_prefixes() - RESERVED_SITE_SLUGS - {"_next"}
    assert not site_only, f"백엔드 예약 목록에 없는 site 예약 경로: {sorted(site_only)}"


def _site_static_route_segments() -> set[str]:
    """`site/app` 최상위의 정적 라우트 — `[slug]`보다 먼저 잡히는 경로들."""
    app_dir = _HOST_ROUTING.parents[1] / "app"
    segments = set()
    for child in app_dir.iterdir():
        name = child.name
        if not child.is_dir() or name[0] in "[_(" or "." in name:
            continue
        if any(child.rglob("page.tsx")) or any(child.rglob("route.ts")):
            segments.add(name)
    return segments


def test_every_static_site_route_is_a_reserved_slug():
    """랜딩 개편처럼 site에 최상위 페이지가 새로 생기면 백엔드 예약 slug에도 들어가야 한다.

    빠지면 그 이름의 slug를 가진 병원이 만들어지고, 플랫폼 도메인에서는 정적 라우트가
    `[slug]`보다 먼저 잡혀 그 병원 페이지가 영원히 열리지 않는다. site의 rewrite 예약
    목록(`RESERVED_PREFIXES`)은 병원 자기 도메인에서 플랫폼 페이지를 보일지의 문제라
    여기서 강제하지 않는다 — `/contact`·`/brochure`는 환자가 보는 병원 도메인에
    내보이지 않는다.
    """
    segments = _site_static_route_segments()
    assert "contact" in segments  # 파싱이 조용히 비지 않았는지
    assert not segments - RESERVED_SITE_SLUGS, sorted(segments - RESERVED_SITE_SLUGS)
