"""Global exception handler behavior (API-2)."""
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.main import _integrity_error_handler


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/hospitals",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
        }
    )


async def test_integrity_error_maps_to_409():
    exc = IntegrityError("INSERT ...", {}, Exception("duplicate key value violates unique constraint"))
    response = await _integrity_error_handler(_request(), exc)
    assert response.status_code == 409
    # Generic body — must not leak the SQL statement or constraint internals.
    assert b"Resource conflict" in response.body
    assert b"unique constraint" not in response.body
