import type { DomainDnsStrategy, DomainManagementMode } from '@/lib/domain'

export interface DomainProfile {
  id?: string
  slug?: string
  aeo_domain?: string
  status?: string
  profile_complete?: boolean
  v0_report_done?: boolean
  site_built?: boolean
  site_live?: boolean
  schedule_set?: boolean
  domain_management_mode?: DomainManagementMode
  domain_dns_strategy?: DomainDnsStrategy
  domain_registrar?: string | null
  domain_dns_provider?: string | null
  domain_purchase_note?: string | null
}

export interface DomainSetupPanelProps {
  hospitalId: string
  profile: DomainProfile
  onProfileChange: (patch: Partial<DomainProfile>) => void
  onHeaderRefresh: () => void
}
