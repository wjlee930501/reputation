import type { DomainDnsStrategy, DomainManagementMode } from '@/lib/domain'
import type { Hospital } from '@/types'

export interface DomainProfile {
  id?: string
  slug?: string
  aeo_domain?: string
  website_url?: string
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
  domain_cert_job_state?: string | null
  domain_cert_job_started_at?: string | null
  domain_cert_dns_verified_at?: string | null
  domain_last_checked_at?: string | null
  domain_last_check_ok?: boolean | null
  domain_last_check_reason?: string | null
}

export interface DomainSetupPanelProps {
  hospitalId: string
  profile: DomainProfile
  activationReadiness: Hospital | null
  onProfileChange: (patch: Partial<DomainProfile>) => void
  onHeaderRefresh: () => void
}
