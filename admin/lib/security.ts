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

export function getLoginRateLimitKey(req: RequestLike): string {
  const runtimeIp = (req as { ip?: unknown }).ip
  if (typeof runtimeIp === 'string' && runtimeIp.trim()) {
    return `ip:${runtimeIp.trim()}`
  }

  return 'global'
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
