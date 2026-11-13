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

/** 상담 요청의 지역 입력. 계약 등록 화면이 같은 지역 레이아웃 분포를 찾는 데만 쓴다. */
export function readRegionFromLeadContext(value: unknown): string | null {
  if (!value || typeof value !== 'object' || !('lead' in value)) return null
  const lead = value.lead
  if (!lead || typeof lead !== 'object' || !('region_keyword' in lead)) return null
  return typeof lead.region_keyword === 'string' && lead.region_keyword.trim()
    ? lead.region_keyword.trim()
    : null
}
