/**
 * 옛 병원 화면 주소 → 새 탭(현황 · 병원 정보 · 콘텐츠 · 보고서) 매핑.
 *
 * 북마크와 Slack에 남은 링크가 죽지 않게 옛 8개 라우트는 redirect만 남긴다.
 * 서버는 `#` 조각을 볼 수 없으므로, 옛 화면이 갖고 있던 위치는 여기서 고정 앵커로
 * 정한다. 대신 서버가 볼 수 있는 query는 딥링크에 필요한 것만 살려서 넘긴다.
 */

export type LegacyHospitalSegment =
  | 'dashboard'
  | 'onboarding'
  | 'profile'
  | 'schedule'
  | 'wiki'
  | 'essence'
  | 'query-targets'
  | 'exposure-actions'

export type LegacySearchParams = Record<string, string | string[] | undefined>

/** 새 화면이 실제로 읽는 파라미터만 넘긴다 — 나머지는 옛 화면 전용이라 버린다. */
const SAFE_QUERY_KEYS = ['content', 'year', 'month', 'leadId'] as const

const TARGETS: Record<LegacyHospitalSegment, { path: string; hash?: string }> = {
  dashboard: { path: '' },
  // 운영 기준의 예외는 이제 현황의 예외 카드가 맡는다.
  essence: { path: '' },
  onboarding: { path: 'info' },
  profile: { path: 'info' },
  wiki: { path: 'info' },
  schedule: { path: 'content', hash: 'content-schedule' },
  'query-targets': { path: 'content', hash: 'content-signals' },
  'exposure-actions': { path: 'content', hash: 'content-signals' },
}

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value
}

export function legacyHospitalRedirect(
  hospitalId: string,
  segment: LegacyHospitalSegment,
  search?: LegacySearchParams,
): string
export function legacyHospitalRedirect(
  hospitalId: string,
  segment: string,
  search?: LegacySearchParams,
): string | null
export function legacyHospitalRedirect(
  hospitalId: string,
  segment: string,
  search: LegacySearchParams = {},
): string | null {
  const target = TARGETS[segment as LegacyHospitalSegment]
  if (!target) return null

  const query = new URLSearchParams()
  for (const key of SAFE_QUERY_KEYS) {
    const value = firstValue(search[key])
    if (value) query.set(key, value)
  }

  // 자기 도메인 안내로 바로 보내던 `profile#domain-setup` 링크만 앵커가 다르다.
  // 서버는 `#`을 못 보므로 `?section=domain`을 붙여 온 링크에만 앵커를 준다.
  const hash =
    segment === 'profile' && firstValue(search.section) === 'domain'
      ? 'domain-setup'
      : target.hash

  const base = target.path
    ? `/hospitals/${hospitalId}/${target.path}`
    : `/hospitals/${hospitalId}`
  const queryString = query.toString()
  return `${base}${queryString ? `?${queryString}` : ''}${hash ? `#${hash}` : ''}`
}
