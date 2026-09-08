import { isPubliclyServing } from './public-service-state.ts'
import type { HospitalStatusValue } from '../types/index.ts'

export function customDomainLiveUrl(input: {
  status?: HospitalStatusValue | null
  site_live?: boolean | null
  aeo_domain?: string | null
  hasUnsavedChange: boolean
}): string | null {
  const domain = input.aeo_domain?.trim() ?? ''
  if (!isPubliclyServing(input) || input.hasUnsavedChange || !domain) return null
  return `https://${domain}`
}
