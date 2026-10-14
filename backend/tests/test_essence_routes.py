"""H-04: 수동 초안 생성 경로는 없다. 보관 경로는 있고, 승인·재검수·판 수정 경로는 410으로 남는다
(Phase 4 D33 — 옛 화면이 부르면 '자동 적용으로 바뀌었다'는 안내를 받는다).

FastAPI 0.139의 `app.routes`는 지연 포함된 `_IncludedRouter` 래퍼라 경로가 보이지
않는다. 실제로 노출되는 계약은 OpenAPI 스펙이므로 그쪽을 읽는다.
"""

from app.main import app


def _admin_paths() -> set[tuple[str, str]]:
    paths: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            paths.add((method.upper(), path))
    return paths


def test_manual_draft_creation_route_is_gone():
    assert (
        "POST",
        "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/draft",
    ) not in _admin_paths()


def test_archive_and_re_review_routes_exist():
    paths = _admin_paths()
    assert (
        "POST",
        "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/archive",
    ) in paths
    assert (
        "POST",
        "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/re-review",
    ) in paths


def test_the_retired_approve_route_still_answers_with_410():
    """경로는 남아 410으로 답한다 — 옛 화면이 404 대신 안내 문구를 받는다."""
    assert (
        "POST",
        "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/approve",
    ) in _admin_paths()
