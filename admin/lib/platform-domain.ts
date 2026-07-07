export const DEFAULT_CNAME_TARGET = 'cname.reputation.motionlabs.kr'

const DEFAULT_SITE_HOST = 'reputation.motionlabs.kr'

function configuredSiteHost(): string | null {
  const raw = process.env.NEXT_PUBLIC_SITE_URL?.trim()
  if (!raw) return null
  try {
    return new URL(raw).hostname.toLowerCase()
  } catch (error) {
    if (error instanceof TypeError) return null
    throw error
  }
}

export function platformSiteHost(): string {
  return configuredSiteHost() ?? DEFAULT_SITE_HOST
}

export function platformSubdomainHost(slug: string | null | undefined): string | null {
  const value = (slug ?? '').trim()
  if (!value) return null
  return `${value}.${platformSiteHost()}`
}

export function platformSubdomainUrl(slug: string | null | undefined): string | null {
  const host = platformSubdomainHost(slug)
  return host ? `https://${host}` : null
}
