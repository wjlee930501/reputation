import { clientIpFromForwardedHeaders } from './client-ip.ts'

type HeaderLike = {
  get(name: string): string | null
}

export function buildLeadOutboundHeaders(headers: HeaderLike): Record<string, string> {
  const outboundHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const clientIp = clientIpFromForwardedHeaders(headers) ?? ''
  if (clientIp) {
    outboundHeaders['X-Forwarded-For'] = clientIp
    outboundHeaders['X-Real-IP'] = clientIp
  }

  const bffSecret = (process.env.SITE_BFF_SECRET || '').trim()
  if (bffSecret) {
    outboundHeaders['X-BFF-Auth'] = bffSecret
    if (clientIp) {
      outboundHeaders['X-Visitor-IP'] = clientIp
    }
  }

  return outboundHeaders
}

export function isLeadValidationUpstreamStatus(status: number): boolean {
  return status >= 400 && status < 500 && status !== 401 && status !== 403 && status !== 429
}
