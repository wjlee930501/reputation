export function buildLeadOnboardingHref(leadId: string): string {
  const params = new URLSearchParams({ leadId })
  return `/hospitals/new?${params.toString()}`
}

export function readClinicNameFromLeadContext(value: unknown): string | null {
  if (!value || typeof value !== 'object' || !('lead' in value)) return null
  const lead = value.lead
  if (!lead || typeof lead !== 'object' || !('clinic_name' in lead)) return null
  return typeof lead.clinic_name === 'string' && lead.clinic_name.trim()
    ? lead.clinic_name.trim()
    : null
}
