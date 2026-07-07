// 커스텀 도메인 → /{slug} rewrite의 순수 판정 로직.
// middleware.ts는 이 모듈을 얇게 감싸기만 한다 (fetch/캐시는 middleware 담당).

/** Host 헤더에서 비교용 hostname만 추출한다 (소문자, 포트 제거, IPv6 브래킷 처리). */
export function normalizeHostname(hostHeader: string | null | undefined): string | null {
  const raw = hostHeader?.trim().toLowerCase()
  if (!raw) return null
  // IPv6 literal: "[::1]:3000" → "::1"
  if (raw.startsWith('[')) {
    const end = raw.indexOf(']')
    return end > 0 ? raw.slice(1, end) : null
  }
  return raw.split(':', 1)[0] || null
}

/**
 * 플랫폼 자체 호스트 목록 — 여기 해당하면 middleware는 손대지 않는다.
 * NEXT_PUBLIC_SITE_URL의 host + 로컬 개발 호스트.
 */
export function getPrimaryHostnames(siteUrl: string | null | undefined): string[] {
  const hosts = ['localhost', '127.0.0.1', '::1']
  if (siteUrl) {
    try {
      hosts.push(new URL(siteUrl).hostname.toLowerCase())
    } catch {
      // 잘못된 SITE_URL은 무시 — 로컬 호스트만으로 판정.
    }
  }
  return hosts
}

const PLATFORM_HOSTNAMES = new Set(['run.app', 'vercel.app'])
const PLATFORM_HOST_SUFFIXES = ['.run.app', '.vercel.app'] as const

function isPlatformRuntimeHost(hostname: string): boolean {
  if (PLATFORM_HOSTNAMES.has(hostname)) return true
  return PLATFORM_HOST_SUFFIXES.some((suffix) => hostname.endsWith(suffix))
}

/** 플랫폼 호스트 여부. host가 비어 있으면 안전하게 primary 취급(rewrite 안 함). */
export function isPrimaryHost(hostHeader: string | null | undefined, primaryHostnames: string[]): boolean {
  const hostname = normalizeHostname(hostHeader)
  if (!hostname) return true
  if (primaryHostnames.includes(hostname)) return true
  return isPlatformRuntimeHost(hostname)
}

function firstForwardedHost(forwardedHostHeader: string | null | undefined): string | null {
  const raw = forwardedHostHeader?.split(',', 1)[0]?.trim()
  return normalizeHostname(raw)
}

export function getEffectiveHost(
  hostHeader: string | null | undefined,
  forwardedHostHeader: string | null | undefined,
  primaryHostnames: string[],
): string | null {
  const hostname = normalizeHostname(hostHeader)
  if (!hostname) return null
  if (!isPrimaryHost(hostname, primaryHostnames)) return hostname
  if (!isPlatformRuntimeHost(hostname)) return hostname

  const forwardedHostname = firstForwardedHost(forwardedHostHeader)
  if (!forwardedHostname || isPrimaryHost(forwardedHostname, primaryHostnames)) return hostname
  return forwardedHostname
}

// 커스텀 도메인에서도 플랫폼 그대로 서빙해야 하는 예약 경로.
const RESERVED_PREFIXES = ['/_next', '/api', '/landing', '/privacy', '/terms']
const RESERVED_EXACT = new Set(['/robots.txt', '/sitemap.xml'])

// 확장자가 있는 경로는 정적 자산으로 보고 손대지 않는다 — 단 /llms.txt는
// 병원별 라우트(/{slug}/llms.txt)가 존재하므로 rewrite 대상이다.
const REWRITABLE_FILE_PATHS = new Set(['/llms.txt'])

export function isReservedPath(pathname: string): boolean {
  if (RESERVED_EXACT.has(pathname)) return true
  if (pathname === '/favicon' || pathname.startsWith('/favicon')) return true
  for (const prefix of RESERVED_PREFIXES) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return true
  }
  if (REWRITABLE_FILE_PATHS.has(pathname)) return false
  // 마지막 세그먼트에 확장자가 있으면 정적 파일로 간주.
  const lastSegment = pathname.slice(pathname.lastIndexOf('/') + 1)
  if (lastSegment.includes('.')) return true
  return false
}

// 백엔드 slug 형식 — 예상 밖의 값이 경로로 주입되지 않도록 화이트리스트 검증.
const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/

/**
 * rewrite 판정 (순수 함수).
 * @returns rewrite할 pathname, 또는 null(그대로 통과).
 *
 * | 조건                                        | 결과                  |
 * |---------------------------------------------|-----------------------|
 * | primary host (플랫폼/로컬/run.app/vercel.app) | null                  |
 * | slug 미해석 (404·백엔드 다운)                | null (middleware 404) |
 * | 예약 경로 (_next/api/landing/legal/정적파일) | null                  |
 * | `/`                                          | `/{slug}`             |
 * | 이미 `/{slug}` 또는 `/{slug}/...`             | null                  |
 * | 그 외 루트 경로 (/contents, /llms.txt 등)    | `/{slug}{path}`       |
 */
export function decideRewrite(
  host: string | null | undefined,
  pathname: string,
  slugOrNull: string | null,
  primaryHostnames: string[],
): string | null {
  if (isPrimaryHost(host, primaryHostnames)) return null
  if (!slugOrNull || !SLUG_PATTERN.test(slugOrNull)) return null
  if (isReservedPath(pathname)) return null

  const slug = slugOrNull
  if (pathname === '/' || pathname === '') return `/${slug}`
  if (pathname === `/${slug}` || pathname.startsWith(`/${slug}/`)) return null
  return `/${slug}${pathname}`
}

export function shouldFailClosedCustomHost(
  host: string | null | undefined,
  pathname: string,
  slugOrNull: string | null,
  primaryHostnames: string[],
): boolean {
  if (isPrimaryHost(host, primaryHostnames)) return false
  if (isReservedPath(pathname)) return false
  return !slugOrNull || !SLUG_PATTERN.test(slugOrNull)
}
