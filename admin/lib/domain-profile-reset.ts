import type { DomainDnsStrategy, DomainManagementMode } from './domain.ts'

export interface DomainProfileResetProfile {
  readonly id?: string
  readonly aeo_domain?: string
  readonly domain_management_mode?: DomainManagementMode
  readonly domain_dns_strategy?: DomainDnsStrategy
  readonly domain_registrar?: string | null
  readonly domain_dns_provider?: string | null
  readonly domain_purchase_note?: string | null
}

export interface DomainProfileResetState {
  readonly profileKey: string
  readonly domainSavedValue: string
  readonly managementMode: DomainManagementMode
  readonly dnsStrategy: DomainDnsStrategy
  readonly registrar: string
  readonly dnsProvider: string
  readonly purchaseNote: string
}

export function getDomainProfileResetState(
  hospitalId: string,
  profile: DomainProfileResetProfile,
  loadedProfileKey: string | null,
): DomainProfileResetState | null {
  const profileKey = profile.id ?? hospitalId
  if (loadedProfileKey === profileKey) return null
  return {
    profileKey,
    domainSavedValue: profile.aeo_domain ?? '',
    managementMode: profile.domain_management_mode ?? 'HOSPITAL_MANAGED',
    dnsStrategy: profile.domain_dns_strategy ?? 'CNAME',
    registrar: profile.domain_registrar ?? '',
    dnsProvider: profile.domain_dns_provider ?? '',
    purchaseNote: profile.domain_purchase_note ?? '',
  }
}
