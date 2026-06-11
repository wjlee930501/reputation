// 병원 허브 페이지의 canonical base URL 정책 (단일 출처 — 페이지별 ad-hoc 분기 금지).
//
// 병원이 자체 도메인(aeo_domain)을 연결하면 그 도메인이 해당 병원 허브의 canonical
// origin이 된다. 경로는 플랫폼과 동일하게 /{slug}/... 를 유지한다 (middleware가
// 커스텀 도메인 요청을 /{slug}{path}로 rewrite하므로 두 표면이 같은 경로 구조를 공유).
// aeo_domain이 없으면 기존 플랫폼 SITE_URL 동작이 그대로 유지된다.

const DEFAULT_SITE_URL = 'https://reputation.co.kr'

/** 플랫폼 공개 표면의 base URL (env 미설정 시 기본 도메인). */
export function platformSiteUrl(): string {
  const value = process.env.NEXT_PUBLIC_SITE_URL?.trim()
  return value ? value.replace(/\/$/, '') : DEFAULT_SITE_URL
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
