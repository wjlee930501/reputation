// 병원 허브 페이지의 canonical base URL 정책 (단일 출처 — 페이지별 ad-hoc 분기 금지).
//
// 병원이 자체 도메인(aeo_domain)을 연결하면 그 도메인이 해당 병원 허브의 canonical
// origin이 된다. 커스텀 도메인의 공개 경로에서는 내부 rewrite용 /{slug}를 노출하지
// 않는다. 플랫폼 도메인에서만 /{slug}/... 경로를 유지한다.
// aeo_domain이 없으면 기존 플랫폼 SITE_URL 동작이 그대로 유지된다.

const DEFAULT_SITE_URL = 'https://reputation.motionlabs.kr'
const LOCAL_HOSTNAMES = new Set(['localhost', '127.0.0.1', '::1'])

/** 플랫폼 공개 표면의 base URL (env 미설정 시 기본 도메인). */
export function platformSiteUrl(): string {
  const value = process.env.NEXT_PUBLIC_SITE_URL?.trim()
  if (value) return normalizePlatformSiteUrl(value)
  if (process.env.NODE_ENV === 'production') {
    throw new Error('NEXT_PUBLIC_SITE_URL must be set in production')
  }
  return DEFAULT_SITE_URL
}

function normalizePlatformSiteUrl(value: string): string {
  const url = new URL(value)
  if (process.env.NODE_ENV === 'production') {
    if (url.protocol !== 'https:') {
      throw new Error('NEXT_PUBLIC_SITE_URL must use https in production')
    }
    if (isLocalHostname(url.hostname)) {
      throw new Error('NEXT_PUBLIC_SITE_URL must use a public hostname in production')
    }
  }
  return url.origin
}

function isLocalHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[(.*)\]$/, '$1')
  return LOCAL_HOSTNAMES.has(normalized) || normalized.endsWith('.localhost')
}

// 도메인으로 허용하는 형태: 영숫자/하이픈 라벨을 점으로 연결한 hostname.
// AE 입력 필드이므로 스킴/경로/포트가 섞여 들어와도 hostname만 추출해 검증한다.
const HOSTNAME_PATTERN = /^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/

/**
 * aeo_domain 자유 입력값을 hostname으로 정규화한다.
 * 유효하지 않으면 null — 호출부는 플랫폼 URL로 폴백한다.
 */
export function normalizeCustomDomain(value: string | null | undefined): string | null {
  if (!value) return null
  let candidate = value.trim().toLowerCase()
  if (!candidate) return null
  candidate = candidate.replace(/^https?:\/\//, '')
  candidate = candidate.split(/[/?#]/, 1)[0]
  candidate = candidate.replace(/:\d+$/, '')
  return HOSTNAME_PATTERN.test(candidate) ? candidate : null
}

interface HospitalWithDomain {
  aeo_domain?: string | null
}

/**
 * 병원 허브 페이지(canonical/OG/JSON-LD/llms.txt/sitemap)에 쓰는 canonical base.
 * - aeo_domain이 유효하면 `https://{aeo_domain}` (공개 payload에 aeo_domain이
 *   내려온다는 것 자체가 해당 도메인으로 서빙 중임을 의미한다)
 * - 없거나 무효하면 플랫폼 SITE_URL — 기존 동작 그대로.
 */
export function canonicalBase(hospital: HospitalWithDomain | null | undefined): string {
  const domain = normalizeCustomDomain(hospital?.aeo_domain)
  return domain ? `https://${domain}` : platformSiteUrl()
}

/** 병원 홈의 canonical URL. 커스텀 도메인은 `/`, 플랫폼은 `/{slug}`가 홈이다. */
export function canonicalHospitalUrl(
  hospital: HospitalWithDomain | null | undefined,
  slug: string,
  suffix = '',
): string {
  const domain = normalizeCustomDomain(hospital?.aeo_domain)
  const base = domain ? `https://${domain}` : `${platformSiteUrl()}/${slug}`
  if (!suffix) return base
  return `${base}/${suffix.replace(/^\/+/, '')}`
}
