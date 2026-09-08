"""H-04: 수동 초안 생성 경로는 없고, 보관·재검수 요청 경로는 있다.

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


def test_the_approve_route_is_still_reachable():
    """보관·재검수 경로 이름이 승인 경로와 같은 접두사·파라미터를 쓰는지 함께 고정한다."""
    assert (
        "POST",
        "/api/v1/admin/hospitals/{hospital_id}/essence/philosophy/{philosophy_id}/approve",
    ) in _admin_paths()
