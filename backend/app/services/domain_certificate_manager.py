"""Certificate Manager 기반 커스텀 도메인 인증서/Map entry 프로비저닝.

공개 함수는 의도적으로 동기 함수다. Admin async endpoint나 Celery async helper에서는
``await asyncio.to_thread(ensure_domain_certificate, hostname)`` 형태로 호출한다. GCP SDK의
동기 gRPC 호출이 event loop를 막지 않게 하는 계약이다.

기존 Terraform 관리 entry는 ID 규칙이 다를 수 있으므로 지정 map의 entry를 hostname으로
먼저 탐색한다. 없을 때만 SHA-256 기반 결정적 ID로 LB authorization managed certificate와
map entry를 생성한다. 모든 오류는 fail-closed 상태로 축약하며 GCP 원문 오류는 외부로
반환하거나 로그에 기록하지 않는다.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Literal

from google.api_core import exceptions as google_exceptions
from google.auth import exceptions as google_auth_exceptions
from google.cloud import certificate_manager_v1

from app.core.config import settings
from app.utils.domain import is_valid_hostname, normalize_domain

logger = logging.getLogger(__name__)

DomainCertificatePhase = Literal[
    "ACTIVE",
    "PROVISIONING",
    "FAILED",
    "NOT_FOUND",
    "INVALID",
    "CONFIG_ERROR",
]

_OPERATION_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class DomainCertificateResult:
    hostname: str
    ready: bool
    phase: DomainCertificatePhase
    certificate_state: str | None
    map_entry_state: str | None
    certificate_name: str | None
    map_entry_name: str | None
    message: str
    error_code: str | None = None


def _resource_ids(hostname: str) -> tuple[str, str]:
    """GCP resource ID 규칙에 맞는 결정적 ID 두 개를 반환한다."""
    digest = hashlib.sha256(hostname.encode("utf-8")).hexdigest()[:12]
    return f"reputation-cert-{digest}", f"reputation-entry-{digest}"


def _resource_paths() -> tuple[str, str] | None:
    project = settings.GCP_PROJECT_ID.strip()
    location = settings.CERTIFICATE_MANAGER_LOCATION.strip()
    map_name = settings.CERTIFICATE_MAP_NAME.strip()
    if not project or not location or not map_name:
        return None

    location_parent = f"projects/{project}/locations/{location}"
    map_path = (
        map_name
        if map_name.startswith("projects/")
        else f"{location_parent}/certificateMaps/{map_name}"
    )
    return location_parent, map_path


def _normalize_hostname(hostname: str) -> str | None:
    normalized = normalize_domain(hostname)
    return normalized if is_valid_hostname(normalized) else None


def _certificate_state(certificate: certificate_manager_v1.Certificate) -> str:
    try:
        return certificate_manager_v1.Certificate.ManagedCertificate.State(
            certificate.managed.state
        ).name
    except (TypeError, ValueError):
        return "STATE_UNSPECIFIED"


def _map_entry_state(entry: certificate_manager_v1.CertificateMapEntry) -> str:
    try:
        return certificate_manager_v1.ServingState(entry.state).name
    except (TypeError, ValueError):
        return "SERVING_STATE_UNSPECIFIED"


def _invalid_result(raw_hostname: str) -> DomainCertificateResult:
    return DomainCertificateResult(
        hostname=raw_hostname.strip().lower(),
        ready=False,
        phase="INVALID",
        certificate_state=None,
        map_entry_state=None,
        certificate_name=None,
        map_entry_name=None,
        message="유효한 공개 도메인 형식이 아닙니다.",
        error_code="INVALID_HOSTNAME",
    )


def _config_error_result(hostname: str) -> DomainCertificateResult:
    return DomainCertificateResult(
        hostname=hostname,
        ready=False,
        phase="CONFIG_ERROR",
        certificate_state=None,
        map_entry_state=None,
        certificate_name=None,
        map_entry_name=None,
        message="인증서 자동 연결 설정이 준비되지 않았습니다. 운영 설정을 확인해 주세요.",
        error_code="CERTIFICATE_MANAGER_CONFIG",
    )


def _api_error_result(hostname: str, error: BaseException) -> DomainCertificateResult:
    # 원문에는 project/resource/credential 관련 정보가 포함될 수 있어 type만 기록한다.
    logger.warning(
        "Certificate Manager operation failed: hostname_hash=%s error_type=%s",
        hashlib.sha256(hostname.encode("utf-8")).hexdigest()[:12],
        type(error).__name__,
    )
    return DomainCertificateResult(
        hostname=hostname,
        ready=False,
        phase="FAILED",
        certificate_state=None,
        map_entry_state=None,
        certificate_name=None,
        map_entry_name=None,
        message="인증서 서비스 확인에 실패했습니다. 잠시 후 다시 시도해 주세요.",
        error_code="CERTIFICATE_MANAGER_API",
    )


def _find_entry_by_hostname(
    client: certificate_manager_v1.CertificateManagerClient,
    map_path: str,
    hostname: str,
) -> certificate_manager_v1.CertificateMapEntry | None:
    for entry in client.list_certificate_map_entries(
        parent=map_path,
        timeout=_OPERATION_TIMEOUT_SECONDS,
    ):
        if normalize_domain(entry.hostname) == hostname:
            return entry
    return None


def _result_from_entry(
    client: certificate_manager_v1.CertificateManagerClient,
    hostname: str,
    entry: certificate_manager_v1.CertificateMapEntry,
) -> DomainCertificateResult:
    map_state = _map_entry_state(entry)
    certificates: list[tuple[str, certificate_manager_v1.Certificate]] = []
    for certificate_name in entry.certificates:
        certificate = client.get_certificate(
            name=certificate_name,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )
        certificates.append((certificate_name, certificate))

    if not certificates:
        return DomainCertificateResult(
            hostname=hostname,
            ready=False,
            phase="FAILED",
            certificate_state=None,
            map_entry_state=map_state,
            certificate_name=None,
            map_entry_name=entry.name or None,
            message="도메인 연결 항목에 인증서가 지정되지 않았습니다.",
            error_code="CERTIFICATE_REFERENCE_MISSING",
        )

    # 여러 인증서가 참조되면 ACTIVE 인증서를 우선한다. 인증서 교체 중에도 기존 ACTIVE
    # 인증서가 있으면 서비스 가능하므로 fail-closed이면서 불필요한 중단을 피한다.
    selected_name, selected_certificate = next(
        (
            (name, certificate)
            for name, certificate in certificates
            if _certificate_state(certificate) == "ACTIVE"
        ),
        certificates[0],
    )
    cert_state = _certificate_state(selected_certificate)
    ready = cert_state == "ACTIVE" and map_state == "ACTIVE"

    if ready:
        phase: DomainCertificatePhase = "ACTIVE"
        message = "HTTPS 인증서와 도메인 연결 항목이 활성화되었습니다."
        error_code = None
    elif cert_state == "FAILED":
        phase = "FAILED"
        message = "HTTPS 인증서 발급에 실패했습니다. DNS 연결 상태를 확인해 주세요."
        error_code = "CERTIFICATE_FAILED"
    else:
        phase = "PROVISIONING"
        message = "HTTPS 인증서를 준비하고 있습니다. DNS 전파 후 다시 확인해 주세요."
        error_code = None

    return DomainCertificateResult(
        hostname=hostname,
        ready=ready,
        phase=phase,
        certificate_state=cert_state,
        map_entry_state=map_state,
        certificate_name=selected_name,
        map_entry_name=entry.name or None,
        message=message,
        error_code=error_code,
    )


def inspect_domain_certificate(
    hostname: str,
    *,
    client: certificate_manager_v1.CertificateManagerClient | None = None,
) -> DomainCertificateResult:
    """기존 map entry와 참조 인증서 상태를 읽기 전용으로 확인한다."""
    normalized = _normalize_hostname(hostname)
    if not normalized:
        return _invalid_result(hostname)
    paths = _resource_paths()
    if not paths:
        return _config_error_result(normalized)
    _, map_path = paths

    try:
        service = client or certificate_manager_v1.CertificateManagerClient()
        entry = _find_entry_by_hostname(service, map_path, normalized)
        if entry is None:
            return DomainCertificateResult(
                hostname=normalized,
                ready=False,
                phase="NOT_FOUND",
                certificate_state=None,
                map_entry_state=None,
                certificate_name=None,
                map_entry_name=None,
                message="이 도메인의 HTTPS 인증서 연결 항목이 아직 없습니다.",
                error_code=None,
            )
        return _result_from_entry(service, normalized, entry)
    except (
        google_exceptions.GoogleAPIError,
        google_auth_exceptions.GoogleAuthError,
        TimeoutError,
    ) as exc:
        return _api_error_result(normalized, exc)


def _get_or_create_certificate(
    client: certificate_manager_v1.CertificateManagerClient,
    location_parent: str,
    hostname: str,
) -> certificate_manager_v1.Certificate:
    certificate_id, _ = _resource_ids(hostname)
    certificate_name = f"{location_parent}/certificates/{certificate_id}"
    try:
        return client.get_certificate(
            name=certificate_name,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )
    except google_exceptions.NotFound:
        pass

    certificate = certificate_manager_v1.Certificate(
        managed=certificate_manager_v1.Certificate.ManagedCertificate(domains=[hostname]),
        labels={"managed-by": "reputation-runtime"},
    )
    try:
        operation = client.create_certificate(
            parent=location_parent,
            certificate_id=certificate_id,
            certificate=certificate,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )
        return operation.result(timeout=_OPERATION_TIMEOUT_SECONDS)
    except google_exceptions.AlreadyExists:
        return client.get_certificate(
            name=certificate_name,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )


def _get_or_create_entry(
    client: certificate_manager_v1.CertificateManagerClient,
    map_path: str,
    hostname: str,
    certificate_name: str,
) -> certificate_manager_v1.CertificateMapEntry:
    _, entry_id = _resource_ids(hostname)
    entry_name = f"{map_path}/certificateMapEntries/{entry_id}"
    try:
        existing = client.get_certificate_map_entry(
            name=entry_name,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )
        if normalize_domain(existing.hostname) != hostname:
            raise ValueError("deterministic certificate map entry collision")
        return existing
    except google_exceptions.NotFound:
        pass

    entry = certificate_manager_v1.CertificateMapEntry(
        hostname=hostname,
        certificates=[certificate_name],
    )
    try:
        operation = client.create_certificate_map_entry(
            parent=map_path,
            certificate_map_entry_id=entry_id,
            certificate_map_entry=entry,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )
        return operation.result(timeout=_OPERATION_TIMEOUT_SECONDS)
    except google_exceptions.AlreadyExists:
        return client.get_certificate_map_entry(
            name=entry_name,
            timeout=_OPERATION_TIMEOUT_SECONDS,
        )


def ensure_domain_certificate(
    hostname: str,
    *,
    client: certificate_manager_v1.CertificateManagerClient | None = None,
) -> DomainCertificateResult:
    """도메인의 managed cert/map entry를 멱등 생성하고 현재 준비 상태를 반환한다."""
    normalized = _normalize_hostname(hostname)
    if not normalized:
        return _invalid_result(hostname)
    paths = _resource_paths()
    if not paths:
        return _config_error_result(normalized)
    location_parent, map_path = paths

    try:
        service = client or certificate_manager_v1.CertificateManagerClient()

        # Terraform 또는 이전 런타임이 만든 hostname entry가 있으면 ID와 무관하게 재사용한다.
        existing = _find_entry_by_hostname(service, map_path, normalized)
        if existing is not None:
            return _result_from_entry(service, normalized, existing)

        certificate = _get_or_create_certificate(service, location_parent, normalized)
        entry = _get_or_create_entry(service, map_path, normalized, certificate.name)
        return _result_from_entry(service, normalized, entry)
    except (
        google_exceptions.GoogleAPIError,
        google_auth_exceptions.GoogleAuthError,
        TimeoutError,
        ValueError,
    ) as exc:
        return _api_error_result(normalized, exc)
