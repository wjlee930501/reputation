"""A single transport failure must not become a misleading DNS instruction."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.services import domain_health_probe as probe
from app.services.dependency_incident_helpers import (
    incident_projection,
    safe_domain_cause,
    safe_domain_next_action,
)
from app.services.notification_messages import build_open_incident_notification

HOST = "clinic.example.test"
HOSPITAL = uuid4()
MARKER = {
    "hospital_id": str(HOSPITAL),
    "slug": "clinic",
    "canonical_host": HOST,
    "release": "site-r1",
}


def check(responses, monkeypatch):
    requests, delays = [], []

    def handler(request):
        requests.append(request)
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(probe.time, "sleep", delays.append)
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        outcome = probe.check_custom_domain_https(
            client, HOST, expected_hospital_id=HOSPITAL, expected_slug="clinic"
        )
    return outcome, requests, delays


@pytest.mark.parametrize(
    "first",
    [
        httpx.ReadTimeout("late"),
        httpx.ConnectError("temporary"),
        *[httpx.Response(code) for code in (408, 429, 500, 502, 503, 504)],
    ],
)
def test_transient_failure_is_rechecked_once_before_recording_failure(first, monkeypatch):
    outcome, requests, delays = check([first, httpx.Response(200, json=MARKER)], monkeypatch)
    assert outcome == (True, "tenant_marker_ok")
    assert len(requests) == 2 and delays == [1.0]
    assert all(str(r.url) == f"https://{HOST}/.well-known/reputation-health" for r in requests)


def test_healthy_endpoint_does_not_get_a_second_request(monkeypatch):
    outcome, requests, delays = check([httpx.Response(200, json=MARKER)], monkeypatch)
    assert outcome[0] and len(requests) == 1 and delays == []


@pytest.mark.parametrize(
    "response,reason",
    [
        (
            httpx.Response(302, headers={"location": "https://other.example"}),
            "redirect_not_allowed",
        ),
        (httpx.Response(200, text="not JSON"), "invalid_tenant_marker"),
        (
            httpx.Response(200, json={**MARKER, "hospital_id": str(uuid4())}),
            "tenant_marker_mismatch",
        ),
        (httpx.Response(404), "http_404"),
        (httpx.Response(401), "http_401"),
    ],
)
def test_identity_and_permanent_failures_still_fail_immediately(response, reason, monkeypatch):
    outcome, requests, delays = check([response], monkeypatch)
    assert outcome == (False, reason)
    assert len(requests) == 1 and not delays


def test_persistent_failure_stops_after_two_attempts(monkeypatch):
    outcome, requests, delays = check([httpx.Response(503), httpx.Response(503)], monkeypatch)
    assert outcome == (False, "http_503") and len(requests) == 2
    assert delays == [1.0]


@pytest.mark.parametrize(
    "header", ["60", "bad", "999999999999999999999999", "Wed, 21 Oct 2037 07:28:00 GMT"]
)
def test_long_or_invalid_retry_after_is_not_ignored(header, monkeypatch):
    outcome, requests, delays = check(
        [httpx.Response(429, headers={"retry-after": header})], monkeypatch
    )
    assert outcome == (False, "http_429") and len(requests) == 1 and not delays


def test_short_retry_after_is_respected(monkeypatch):
    outcome, _, delays = check(
        [httpx.Response(503, headers={"retry-after": "2"}), httpx.Response(200, json=MARKER)],
        monkeypatch,
    )
    assert outcome[0] and delays == [2.0]


def test_rechecked_response_cannot_switch_to_another_hospital(monkeypatch):
    outcome, _, _ = check(
        [httpx.ReadTimeout("late"), httpx.Response(200, json={**MARKER, "slug": "wrong"})],
        monkeypatch,
    )
    assert outcome == (False, "tenant_marker_mismatch")


@pytest.mark.parametrize("reason", ["timeout", "http_429", "http_503", "tenant_marker_mismatch"])
def test_notification_preserves_the_stored_failure_instead_of_generic_text(reason):
    incident = SimpleNamespace(
        id=uuid4(),
        hospital_id=HOSPITAL,
        severity="HIGH",
        customer_impact="공개 주소 확인 필요",
        next_action=safe_domain_next_action(reason),
        admin_path=f"/hospitals/{HOSPITAL}/onboarding",
        version=1,
        episode_seq=1,
        incident_type="DOMAIN_UNHEALTHY",
        safe_error_code="DOMAIN_UNHEALTHY",
        safe_error_message=safe_domain_cause(reason),
    )
    projection = incident_projection(incident, "검증 의원", uuid4(), "확인 필요")
    intent = build_open_incident_notification(projection, "https://admin.example.test")
    assert safe_domain_cause(reason) in intent.message.fallback_text
    assert "자동 작업이 완료되지 않았습니다." not in intent.message.fallback_text
    assert "DNS 확인하고 운영 시작" not in projection.next_action
