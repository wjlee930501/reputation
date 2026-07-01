import type { AdminSession } from './session.ts'
import { ADMIN_CSRF_HEADER_NAME, isStateChangingMethod } from './csrf.ts'

export { isStateChangingMethod } from './csrf.ts'

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
  // headers. Deployment is GCP Cloud Run behind the external Application LB only:
  // 1. X-Forwarded-For is the primary source: the LB appends "<client-ip>, <lb-ip>"
  //    to whatever the client supplied, so the real client is the SECOND-FROM-RIGHT
  //    entry. Leftmost entries are spoofable. (Cloud Run ingress is
  //    INTERNAL_LOAD_BALANCER, so traffic always passes the LB in production;
  //    a direct single-entry XFF only happens in dev.)
  // 2. x-real-ip / x-vercel-forwarded-for are last-resort fallbacks used ONLY when
  //    no XFF header exists at all — GCLB does not strip inbound x-real-ip, so a
  //    client can forge it per request. Preferring it would let an attacker pick
  //    the rate-limit key. Same policy as site/lib/client-ip.ts.
  const forwarded = headers.get('x-forwarded-for')
  if (forwarded) {
    const entries = forwarded.split(',').map((entry) => entry.trim()).filter(Boolean)
    const candidate = entries.length >= 2 ? entries[entries.length - 2] : entries[0]
    // When XFF is present, only its result is trusted — never fall through to the
    // forgeable x-real-ip on a parse failure.
    return candidate && isLikelyIp(candidate) ? candidate : null
  }

  const fallbackIp = (
    headers.get('x-real-ip') ||
    headers.get('x-vercel-forwarded-for') ||
    ''
  ).trim()
  if (fallbackIp && isLikelyIp(fallbackIp)) {
    return fallbackIp
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

export function hasValidSameOrigin(req: RequestLike): boolean {
  if (!isStateChangingMethod(req.method ?? 'GET')) return true

  const origin = req.headers.get('origin')
  if (!origin) return false

  // nextUrl.origin은 쓰지 않는다 — Next standalone(Cloud Run)에서 host가
  // localhost:<PORT>로 치환돼 모든 비교가 실패한다(전 요청 403). 대신 프록시/LB가
  // 설정하는 forwarded 헤더(없으면 Host)로 기대 origin을 구성한다. CSRF 방어
  // 목적상 비교 대상은 "브라우저가 보낸 Origin == 요청이 도착한 host"면 충분하다.
  const expectedHost = (req.headers.get('x-forwarded-host') || req.headers.get('host') || '')
    .split(',')[0]
    .trim()
  if (!expectedHost) return false
  const forwardedProto = (req.headers.get('x-forwarded-proto') || '').split(',')[0].trim()
  const expectedProto = forwardedProto || req.nextUrl?.origin?.split(':')[0] || 'https'

  try {
    const parsed = new URL(origin)
    return parsed.host === expectedHost && parsed.protocol === `${expectedProto}:`
  } catch (error) {
    if (!(error instanceof TypeError)) throw error
    return false
  }
}

export function hasValidAdminCsrfToken(req: RequestLike, session: AdminSession): boolean {
  if (!isStateChangingMethod(req.method ?? 'GET')) return true

  const expected = session.csrfToken
  const actual = req.headers.get(ADMIN_CSRF_HEADER_NAME)
  return Boolean(expected && actual && actual === expected)
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
