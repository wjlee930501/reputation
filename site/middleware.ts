import { NextResponse, type NextRequest } from 'next/server'

import { getApiBase } from '@/lib/config'
import {
  decideRewrite,
  getPrimaryHostnames,
  isPrimaryHost,
  isReservedPath,
  normalizeHostname,
  shouldFailClosedCustomHost,
} from '@/lib/host-routing'

// 병원 커스텀 도메인(CNAME → 플랫폼 LB) 요청을 /{slug} 허브로 rewrite하는 어댑터.
// 판정 로직은 lib/host-routing.ts(순수 함수)에 있고 여기서는 호스트 해석만 한다.
//
// 캐시는 인스턴스(모듈)별 in-memory Map — Cloud Run 다중 인스턴스 간 공유되지 않지만
// 도메인→slug 매핑은 변경이 드물고 TTL이 짧아 인스턴스별 캐싱으로 충분하다.
const POSITIVE_TTL_MS = 5 * 60 * 1000 // 매핑 존재: 5분
const NEGATIVE_TTL_MS = 60 * 1000 // 미등록 도메인(404): 60초 — 신규 연결이 금방 반영되도록 짧게
const RESOLVE_TIMEOUT_MS = 2000

interface CacheEntry {
  slug: string | null
  expiresAt: number
}

const domainSlugCache = new Map<string, CacheEntry>()

async function resolveSlugForDomain(hostname: string): Promise<string | null> {
  const cached = domainSlugCache.get(hostname)
  if (cached && cached.expiresAt > Date.now()) return cached.slug

  const apiBase = getApiBase(false)
  if (!apiBase) return null

  try {
    const res = await fetch(
      `${apiBase}/site/hospitals/by-domain/${encodeURIComponent(hostname)}`,
      { signal: AbortSignal.timeout(RESOLVE_TIMEOUT_MS) },
    )
    if (res.status === 404) {
      domainSlugCache.set(hostname, { slug: null, expiresAt: Date.now() + NEGATIVE_TTL_MS })
      return null
    }
    if (!res.ok) {
      return null
    }
    const data = (await res.json()) as { slug?: unknown }
    const slug = typeof data.slug === 'string' && data.slug ? data.slug : null
    domainSlugCache.set(hostname, {
      slug,
      expiresAt: Date.now() + (slug ? POSITIVE_TTL_MS : NEGATIVE_TTL_MS),
    })
    return slug
  } catch {
    return null
  }
}

export async function middleware(request: NextRequest) {
  const host = request.headers.get('host')
  const primaryHostnames = getPrimaryHostnames(process.env.NEXT_PUBLIC_SITE_URL)
  if (isPrimaryHost(host, primaryHostnames)) return NextResponse.next()

  const pathname = request.nextUrl.pathname
  // 예약 경로는 백엔드 조회 없이 즉시 통과.
  if (isReservedPath(pathname)) return NextResponse.next()

  const hostname = normalizeHostname(host)
  if (!hostname) return NextResponse.next()

  const slug = await resolveSlugForDomain(hostname)
  if (shouldFailClosedCustomHost(host, pathname, slug, primaryHostnames)) {
    return new NextResponse('Not found', { status: 404 })
  }

  const rewritePath = decideRewrite(host, pathname, slug, primaryHostnames)
  if (!rewritePath) return NextResponse.next()

  const url = request.nextUrl.clone()
  url.pathname = rewritePath
  return NextResponse.rewrite(url)
}

export const config = {
  // 정적 자산은 미들웨어 자체를 건너뛴다 (예약 경로 판정은 isReservedPath가 한 번 더 수행).
  matcher: ['/((?!_next/static|_next/image|favicon.ico|landing/).*)'],
}
