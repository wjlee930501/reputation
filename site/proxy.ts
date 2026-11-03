import { NextResponse, type NextRequest } from 'next/server.js'

import { getApiBase } from './lib/config.ts'
import {
  canonicalHostRedirectEnabled,
  decideCanonicalHostTarget,
  decideCanonicalRedirect,
  decideRewrite,
  getPrimaryHostnames,
  isPrimaryHost,
  isReservedPath,
  normalizeHostname,
  platformSlugPathCandidate,
  resolveRequestHost,
  shouldFailClosedCustomHost,
} from './lib/host-routing.ts'
import { aiTrafficEvent, logAiTraffic } from './lib/ai-traffic.ts'
import { canonicalBase } from './lib/site-url.ts'

// 병원 커스텀 도메인(CNAME → 플랫폼 LB) 요청을 /{slug} 허브로 rewrite하는 어댑터.
// 판정 로직은 lib/host-routing.ts(순수 함수)에 있고 여기서는 호스트 해석만 한다.
//
// 캐시는 인스턴스(모듈)별 in-memory Map — Cloud Run 다중 인스턴스 간 공유되지 않는다.
// 도메인 변경 직후 admin revalidate가 이 Map을 직접 무효화할 수 없으므로 positive TTL도 짧게 둔다.
const POSITIVE_TTL_MS = 60 * 1000 // 매핑 존재: 60초
const NEGATIVE_TTL_MS = 60 * 1000 // 미등록 도메인(404): 60초 — 신규 연결이 금방 반영되도록 짧게
// API가 일시 장애여도 마지막으로 검증된 positive 매핑만 24시간 제한으로 사용한다.
// negative 결과는 stale로 쓰지 않아 다른 병원의 slug가 신규 도메인에 노출될 수 없다.
const STALE_POSITIVE_TTL_MS = 24 * 60 * 60 * 1000
const RESOLVE_TIMEOUT_MS = 5000

interface CacheEntry {
  slug: string | null
  // 병원 자기 도메인. 정본 호스트 리다이렉트만 쓴다 — 없거나 모르면 리다이렉트하지 않는다.
  aeoDomain?: string | null
  freshUntil: number
  staleUntil: number
}

const domainSlugCache = new Map<string, CacheEntry>()

type DomainResolveResult =
  | { status: 'found'; slug: string; aeoDomain: string | null }
  | { status: 'not-found' }
  | { status: 'unavailable'; staleSlug: string | null }

function stalePositiveSlug(hostname: string): string | null {
  const cached = domainSlugCache.get(hostname)
  if (!cached?.slug || cached.staleUntil <= Date.now()) return null
  return cached.slug
}

function unavailableResult(hostname: string): DomainResolveResult {
  return { status: 'unavailable', staleSlug: stalePositiveSlug(hostname) }
}

function isExpectedDomainLookupFailure(error: unknown): boolean {
  return error instanceof DOMException || error instanceof TypeError || error instanceof SyntaxError
}

async function resolveSlugForDomain(hostname: string): Promise<DomainResolveResult> {
  const cached = domainSlugCache.get(hostname)
  if (cached && cached.freshUntil > Date.now()) {
    return cached.slug
      ? { status: 'found', slug: cached.slug, aeoDomain: cached.aeoDomain ?? null }
      : { status: 'not-found' }
  }

  const apiBase = getApiBase(false)
  if (!apiBase) return unavailableResult(hostname)

  try {
    const res = await fetch(
      `${apiBase}/site/hospitals/by-domain/${encodeURIComponent(hostname)}`,
      { signal: AbortSignal.timeout(RESOLVE_TIMEOUT_MS) },
    )
    if (res.status === 404) {
      const expiresAt = Date.now() + NEGATIVE_TTL_MS
      domainSlugCache.set(hostname, {
        slug: null,
        freshUntil: expiresAt,
        staleUntil: expiresAt,
      })
      return { status: 'not-found' }
    }
    if (!res.ok) {
      return unavailableResult(hostname)
    }
    const data: unknown = await res.json()
    const slug = typeof data === 'object' && data !== null && 'slug' in data && typeof data.slug === 'string' && data.slug ? data.slug : null
    if (!slug) {
      return unavailableResult(hostname)
    }
    const aeoDomain =
      typeof data === 'object' && data !== null && 'aeo_domain' in data && typeof data.aeo_domain === 'string'
        ? normalizeHostname(data.aeo_domain)
        : null
    domainSlugCache.set(hostname, {
      slug,
      aeoDomain,
      freshUntil: Date.now() + POSITIVE_TTL_MS,
      staleUntil: Date.now() + STALE_POSITIVE_TTL_MS,
    })
    return { status: 'found', slug, aeoDomain }
  } catch (error) {
    if (!isExpectedDomainLookupFailure(error)) throw error
    return unavailableResult(hostname)
  }
}

export function __clearDomainSlugCacheForTest(): void {
  domainSlugCache.clear()
}

export function __setDomainSlugCacheEntryForTest(hostname: string, entry: CacheEntry): void {
  domainSlugCache.set(hostname, entry)
}

function canonicalHostTargetFor(
  requestHostname: string,
  slug: string,
  aeoDomain: string | null,
): string | null {
  if (!canonicalHostRedirectEnabled(process.env.SITE_CANONICAL_HOST_REDIRECT)) return null
  let origin: string
  try {
    origin = canonicalBase({ aeo_domain: aeoDomain }, slug)
  } catch {
    return null
  }
  return decideCanonicalHostTarget(requestHostname, origin)
}

function permanentRedirect(request: NextRequest, origin: string, pathname: string): NextResponse {
  const url = new URL(`${pathname}${request.nextUrl.search}`, origin)
  return NextResponse.redirect(url, 308)
}

/**
 * 플랫폼 호스트의 `/{slug}/...`를 병원 정본 호스트로 영구 이동한다.
 *
 * 운영 플랫폼 호스트(NEXT_PUBLIC_SITE_URL의 DNS 이름)에서만 동작한다. 로컬·run.app 호스트는
 * 경로형이 정상 접근 경로이므로 손대지 않는다. 역조회가 병원을 확정하지 못하면(404·장애)
 * 아무것도 바꾸지 않는다 — 페이지 자체의 404/503 처리를 그대로 둔다.
 */
async function platformPathCanonicalRedirect(
  request: NextRequest,
  effectiveHost: string | null,
): Promise<NextResponse | null> {
  if (!canonicalHostRedirectEnabled(process.env.SITE_CANONICAL_HOST_REDIRECT)) return null
  const hostname = normalizeHostname(effectiveHost)
  const platformHostname = platformDnsHostname()
  if (!hostname || !platformHostname || hostname !== platformHostname) return null

  const candidate = platformSlugPathCandidate(request.nextUrl.pathname)
  if (!candidate) return null

  const resolution = await resolveSlugForDomain(`${candidate.slug}.${platformHostname}`)
  if (resolution.status !== 'found' || resolution.slug !== candidate.slug) return null
  const target = canonicalHostTargetFor(hostname, resolution.slug, resolution.aeoDomain)
  if (!target) return null
  return permanentRedirect(request, target, candidate.rest)
}

function platformDnsHostname(): string | null {
  const value = process.env.NEXT_PUBLIC_SITE_URL?.trim()
  if (!value) return null
  let hostname: string
  try {
    hostname = new URL(value).hostname.toLowerCase()
  } catch {
    return null
  }
  if (!hostname.includes('.') || hostname === 'localhost' || hostname.endsWith('.localhost')) return null
  if (/^[0-9.]+$/.test(hostname) || hostname.includes(':')) return null
  return hostname
}

export async function proxy(request: NextRequest) {
  const host = request.headers.get('host')
  const primaryHostnames = getPrimaryHostnames(process.env.NEXT_PUBLIC_SITE_URL)
  const effectiveHost = resolveRequestHost(
    host,
    request.headers.get('x-forwarded-host'),
    primaryHostnames,
  )
  logAiTraffic(
    aiTrafficEvent({
      host: effectiveHost,
      pathname: request.nextUrl.pathname,
      userAgent: request.headers.get('user-agent'),
      referer: request.headers.get('referer'),
      utmSource: request.nextUrl.searchParams.get('utm_source'),
    }),
  )
  if (isPrimaryHost(effectiveHost, primaryHostnames)) {
    return (await platformPathCanonicalRedirect(request, effectiveHost)) ?? NextResponse.next()
  }

  const pathname = request.nextUrl.pathname
  // 예약 경로는 백엔드 조회 없이 즉시 통과.
  if (isReservedPath(pathname)) return NextResponse.next()

  const hostname = normalizeHostname(effectiveHost)
  if (!hostname) return NextResponse.next()

  const resolution = await resolveSlugForDomain(hostname)
  if (resolution.status === 'unavailable' && !resolution.staleSlug) {
    const res = new NextResponse('Custom domain lookup temporarily unavailable', { status: 503 })
    res.headers.set('cache-control', 'no-store')
    res.headers.set('retry-after', '30')
    return res
  }

  const slug =
    resolution.status === 'found'
      ? resolution.slug
      : resolution.status === 'unavailable'
        ? resolution.staleSlug
        : null

  if (shouldFailClosedCustomHost(effectiveHost, pathname, slug, primaryHostnames)) {
    const res = new NextResponse('Not found', { status: 404 })
    res.headers.set('cache-control', 'no-store')
    return res
  }

  // 관리형 서브도메인에서 자기 도메인으로만 옮긴다. 자기 도메인을 모르면(옛 캐시·응답에 값 없음)
  // 지금 호스트를 정본으로 두고 아무것도 하지 않는다.
  const hostTarget =
    resolution.status === 'found' && resolution.aeoDomain
      ? canonicalHostTargetFor(hostname, resolution.slug, resolution.aeoDomain)
      : null
  if (hostTarget) {
    const publicPath = decideCanonicalRedirect(effectiveHost, pathname, slug, primaryHostnames) ?? pathname
    return permanentRedirect(request, hostTarget, publicPath)
  }

  const canonicalRedirectPath = decideCanonicalRedirect(
    effectiveHost,
    pathname,
    slug,
    primaryHostnames,
  )
  if (canonicalRedirectPath) {
    const url = request.nextUrl.clone()
    url.pathname = canonicalRedirectPath
    return NextResponse.redirect(url, 308)
  }

  const rewritePath = decideRewrite(effectiveHost, pathname, slug, primaryHostnames)
  if (!rewritePath) return NextResponse.next()

  const url = request.nextUrl.clone()
  url.pathname = rewritePath
  return NextResponse.rewrite(url)
}

export const config = {
  // 정적 자산은 미들웨어 자체를 건너뛴다 (예약 경로 판정은 isReservedPath가 한 번 더 수행).
  matcher: ['/((?!_next/static|_next/image|favicon.ico|landing/).*)'],
}
