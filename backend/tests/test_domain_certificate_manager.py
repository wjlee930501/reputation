"""Certificate Manager runtime provisioning service unit tests."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest
from google.api_core import exceptions as google_exceptions
from google.cloud import certificate_manager_v1

from app.services import domain_certificate_manager as certificate_service


HOSTNAME = "clinic.example.com"
PROJECT_ID = "certificate-test-project"
LOCATION = "global"
MAP_NAME = "reputation-certmap"
LOCATION_PARENT = f"projects/{PROJECT_ID}/locations/{LOCATION}"
MAP_PATH = f"{LOCATION_PARENT}/certificateMaps/{MAP_NAME}"


class _FakeOperation:
    def __init__(self, resource: Any) -> None:
        self.resource = resource

    def result(self, timeout: int | None = None) -> Any:
        del timeout
        return self.resource


class _FakeCertificateManagerClient:
    def __init__(self) -> None:
        self.certificates: dict[str, certificate_manager_v1.Certificate] = {}
        self.entries: dict[str, certificate_manager_v1.CertificateMapEntry] = {}
        self.create_certificate_calls = 0
        self.create_entry_calls = 0
        self.list_error: BaseException | None = None

    def list_certificate_map_entries(
        self,
        *,
        parent: str,
        timeout: int,
    ) -> Iterable[certificate_manager_v1.CertificateMapEntry]:
        del parent, timeout
        if self.list_error is not None:
            raise self.list_error
        return list(self.entries.values())

    def get_certificate(
        self,
        *,
        name: str,
        timeout: int,
    ) -> certificate_manager_v1.Certificate:
        del timeout
        try:
            return self.certificates[name]
        except KeyError as exc:
            raise google_exceptions.NotFound("certificate missing") from exc

    def get_certificate_map_entry(
        self,
        *,
        name: str,
        timeout: int,
    ) -> certificate_manager_v1.CertificateMapEntry:
        del timeout
        try:
            return self.entries[name]
        except KeyError as exc:
            raise google_exceptions.NotFound("entry missing") from exc

    def create_certificate(
        self,
        *,
        parent: str,
        certificate_id: str,
        certificate: certificate_manager_v1.Certificate,
        timeout: int,
    ) -> _FakeOperation:
        del timeout
        self.create_certificate_calls += 1
        name = f"{parent}/certificates/{certificate_id}"
        if name in self.certificates:
            raise google_exceptions.AlreadyExists("certificate already exists")
        certificate.name = name
        certificate.managed.state = (
            certificate_manager_v1.Certificate.ManagedCertificate.State.PROVISIONING
        )
        self.certificates[name] = certificate
        return _FakeOperation(certificate)

    def create_certificate_map_entry(
        self,
        *,
        parent: str,
        certificate_map_entry_id: str,
        certificate_map_entry: certificate_manager_v1.CertificateMapEntry,
        timeout: int,
    ) -> _FakeOperation:
        del timeout
        self.create_entry_calls += 1
        name = f"{parent}/certificateMapEntries/{certificate_map_entry_id}"
        if name in self.entries:
            raise google_exceptions.AlreadyExists("entry already exists")
        certificate_map_entry.name = name
        certificate_map_entry.state = certificate_manager_v1.ServingState.PENDING
        self.entries[name] = certificate_map_entry
        return _FakeOperation(certificate_map_entry)


@pytest.fixture(autouse=True)
def _certificate_manager_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(certificate_service.settings, "GCP_PROJECT_ID", PROJECT_ID)
    monkeypatch.setattr(
        certificate_service.settings,
        "CERTIFICATE_MANAGER_LOCATION",
        LOCATION,
    )
    monkeypatch.setattr(
        certificate_service.settings,
        "CERTIFICATE_MAP_NAME",
        MAP_NAME,
    )


def _active_certificate(name: str) -> certificate_manager_v1.Certificate:
    return certificate_manager_v1.Certificate(
        name=name,
        managed=certificate_manager_v1.Certificate.ManagedCertificate(
            domains=[HOSTNAME],
            state=certificate_manager_v1.Certificate.ManagedCertificate.State.ACTIVE,
        ),
    )


def _active_entry(
    name: str,
    certificate_name: str,
) -> certificate_manager_v1.CertificateMapEntry:
    return certificate_manager_v1.CertificateMapEntry(
        name=name,
        hostname=HOSTNAME,
        certificates=[certificate_name],
        state=certificate_manager_v1.ServingState.ACTIVE,
    )


def test_existing_active_terraform_entry_is_reused_by_hostname() -> None:
    client = _FakeCertificateManagerClient()
    certificate_name = f"{LOCATION_PARENT}/certificates/terraform-managed-cert"
    entry_name = f"{MAP_PATH}/certificateMapEntries/terraform-managed-entry"
    client.certificates[certificate_name] = _active_certificate(certificate_name)
    client.entries[entry_name] = _active_entry(entry_name, certificate_name)

    result = certificate_service.ensure_domain_certificate(HOSTNAME, client=client)

    assert result.ready is True
    assert result.phase == "ACTIVE"
    assert result.certificate_state == "ACTIVE"
    assert result.map_entry_state == "ACTIVE"
    assert result.certificate_name == certificate_name
    assert result.map_entry_name == entry_name
    assert result.error_code is None
    assert client.create_certificate_calls == 0
    assert client.create_entry_calls == 0


def test_create_returns_provisioning_until_certificate_and_entry_are_active() -> None:
    client = _FakeCertificateManagerClient()

    result = certificate_service.ensure_domain_certificate(
        f"https://{HOSTNAME}/appointments",
        client=client,
    )

    certificate_id, entry_id = certificate_service._resource_ids(HOSTNAME)
    assert result.hostname == HOSTNAME
    assert result.ready is False
    assert result.phase == "PROVISIONING"
    assert result.certificate_state == "PROVISIONING"
    assert result.map_entry_state == "PENDING"
    assert result.certificate_name == f"{LOCATION_PARENT}/certificates/{certificate_id}"
    assert result.map_entry_name == f"{MAP_PATH}/certificateMapEntries/{entry_id}"
    assert client.create_certificate_calls == 1
    assert client.create_entry_calls == 1


def test_retry_is_idempotent_and_becomes_active_without_recreating_resources() -> None:
    client = _FakeCertificateManagerClient()
    first = certificate_service.ensure_domain_certificate(HOSTNAME, client=client)
    assert first.ready is False

    created_certificate = next(iter(client.certificates.values()))
    created_entry = next(iter(client.entries.values()))
    created_certificate.managed.state = (
        certificate_manager_v1.Certificate.ManagedCertificate.State.ACTIVE
    )
    created_entry.state = certificate_manager_v1.ServingState.ACTIVE

    second = certificate_service.ensure_domain_certificate(HOSTNAME, client=client)

    assert second.ready is True
    assert second.phase == "ACTIVE"
    assert client.create_certificate_calls == 1
    assert client.create_entry_calls == 1


def test_api_failure_is_fail_closed_and_does_not_leak_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _FakeCertificateManagerClient()
    secret = "super-secret-credential-token"
    client.list_error = google_exceptions.ServiceUnavailable(
        f"upstream refused credential={secret}"
    )

    result = certificate_service.ensure_domain_certificate(HOSTNAME, client=client)

    assert result.ready is False
    assert result.phase == "FAILED"
    assert result.error_code == "CERTIFICATE_MANAGER_API"
    assert result.certificate_state is None
    assert result.map_entry_state is None
    assert "실패" in result.message
    assert secret not in result.message
    assert secret not in caplog.text
