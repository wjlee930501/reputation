const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

type HeaderLike = {
  get(name: string): string | null
}

type RequestLike = {
  headers: HeaderLike
  method?: string
  nextUrl?: {
    origin: string
  }
}

function isLikelyIp(value: string): boolean {
  // IPv4 dotted-quad or IPv6 (loose check — enough to reject junk before keying).
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(value)) return true
  return value.includes(':') && /^[0-9a-fA-F:.]+$/.test(value)
}

export function clientIpFromForwardedHeaders(headers: HeaderLike): string | null {
  // NextRequest has no `.ip` in Next 16, so derive the client IP from proxy
  // headers, preferring platform-controlled values over client-controllable ones:
  // 1. x-vercel-forwarded-for / x-real-ip — set by the platform when on Vercel.
  // 2. X-Forwarded-For behind the GCP external Application LB: the LB appends
  //    "<client-ip>, <lb-ip>" to whatever the client supplied, so the real
  //    client is the SECOND-FROM-RIGHT entry. Leftmost entries are spoofable.
  //    (Cloud Run ingress is INTERNAL_LOAD_BALANCER, so traffic always passes
  //    the LB in production; a direct single-entry XFF only happens in dev.)
  const platformIp = (
    headers.get('x-vercel-forwarded-for') ||
    headers.get('x-real-ip') ||
    ''
  ).trim()
  if (platformIp && isLikelyIp(platformIp)) {
    return platformIp
  }

  const forwarded = headers.get('x-forwarded-for')
  if (forwarded) {
    const entries = forwarded.split(',').map((entry) => entry.trim()).filter(Boolean)
    const candidate = entries.length >= 2 ? entries[entries.length - 2] : entries[0]
    if (candidate && isLikelyIp(candidate)) {
      return candidate
    }
  }
  return null
}

export function getLoginRateLimitKey(req: RequestLike): string | null {
  const ip = clientIpFromForwardedHeaders(req.headers)
  if (ip) {
    return `ip:${ip}`
  }

  // Fail open per-request rather than a shared 'global' bucket: returning null
  // means this request is not counted against any shared counter, so one client
  // can never lock out every admin.
  return null
}

export function isStateChangingMethod(method: string): boolean {
  return !SAFE_METHODS.has(method.toUpperCase())
}

export function hasValidSameOrigin(req: RequestLike): boolean {
  if (!isStateChangingMethod(req.method ?? 'GET')) return true

  const expectedOrigin = req.nextUrl?.origin
  const origin = req.headers.get('origin')
  if (!expectedOrigin || !origin) return false

  try {
    return new URL(origin).origin === expectedOrigin
  } catch {
    return false
  }
}

export function buildSafeAdminProxyPath(pathSegments: string[], allowedPrefixes: readonly string[]): string | null {
  if (pathSegments.length === 0) return null

  for (const segment of pathSegments) {
    if (!isSafePathSegment(segment)) return null
  }

  const firstSegment = pathSegments[0]
  if (!allowedPrefixes.includes(firstSegment)) return null

  return pathSegments.map((segment) => encodeURIComponent(segment)).join('/')
}

function isSafePathSegment(segment: string): boolean {
  if (!segment || segment === '.' || segment === '..') return false
  return !segment.includes('/') && !segment.includes('\\')
}
