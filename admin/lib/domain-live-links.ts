export function customDomainLiveUrl(input: {
  site_live?: boolean | null
  aeo_domain?: string | null
  hasUnsavedChange: boolean
}): string | null {
  const domain = input.aeo_domain?.trim() ?? ''
  if (!input.site_live || input.hasUnsavedChange || !domain) return null
  return `https://${domain}`
}
