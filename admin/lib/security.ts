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

export function getLoginRateLimitKey(req: RequestLike): string | null {
  // NextRequest has no `.ip` in Next 16, so derive the client IP from trusted
  // edge headers. On Vercel x-vercel-forwarded-for / x-real-ip are set by the
  // platform; x-forwarded-for is client-controllable, so only its leftmost
  // entry is used as a last resort.
  const candidates = [
    req.headers.get('x-vercel-forwarded-for'),
    req.headers.get('x-real-ip'),
    req.headers.get('x-forwarded-for')?.split(',')[0],
  ]

  for (const candidate of candidates) {
    const ip = candidate?.trim()
    if (ip && isLikelyIp(ip)) {
      return `ip:${ip}`
    }
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
