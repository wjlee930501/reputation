from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hospital import Hospital, HospitalStatus
from app.services.audit_log import default_actor, write_audit_log
from app.services.domain_dns import DomainDnsCheck
from app.services.hospital_lifecycle import ActivationGateSnapshot


async def audit_activation(
    db: AsyncSession,
    hospital: Hospital,
    domain: str,
    dns_check: DomainDnsCheck,
    previous_status: str,
    gate: ActivationGateSnapshot,
) -> None:
    await write_audit_log(
        db,
        action="verify_domain",
        hospital_id=hospital.id,
        actor=default_actor(),
        target_type="domain",
        target_id=domain,
        detail={
            "verified": True,
            "cname_value": dns_check.cname_value,
            "address_values": dns_check.address_values,
            "expected_cname": dns_check.expected_cname,
            "expected_addresses": dns_check.expected_addresses,
            "verification_method": dns_check.verification_method,
            "previous_status": previous_status,
            "previous_site_live": False,
            "new_status": HospitalStatus.ACTIVE.value,
            "new_site_live": True,
            "activation_gate": gate,
        },
    )
