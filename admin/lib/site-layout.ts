import { ADMIN_COPY } from './admin-copy.ts'

/** 병원 공개 페이지 레이아웃. backend `Hospital.site_layout`과 같은 값이다. */
export type SiteLayout = 'A' | 'B' | 'C'

export const SITE_LAYOUTS: readonly SiteLayout[] = ['A', 'B', 'C']

export function isSiteLayout(value: unknown): value is SiteLayout {
  return typeof value === 'string' && (SITE_LAYOUTS as readonly string[]).includes(value)
}

/** 같은 시·도 공개 병원의 레이아웃 수 — `GET /admin/hospitals/site-layout-distribution`. */
export interface SiteLayoutDistribution {
  province: string | null
  counts: Record<SiteLayout | 'unset', number>
  total: number
}

/**
 * 분포 조회 주소. 주소가 있으면 주소 첫 토큰의 시·도를, 없으면(계약 등록) 지역 입력을 쓴다.
 * 바꾸는 병원 자신은 빼고 센다.
 */
export function layoutDistributionPath(input: {
  address?: string | null
  region?: readonly (string | null | undefined)[] | null
  excludeHospitalId?: string | null
}): string {
  const params = new URLSearchParams()
  const address = input.address?.trim()
  if (address) params.set('address', address)
  for (const entry of input.region ?? []) {
    const value = entry?.trim()
    if (value) params.append('region', value)
  }
  if (input.excludeHospitalId) params.set('exclude_hospital_id', input.excludeHospitalId)
  const query = params.toString()
  return `/admin/hospitals/site-layout-distribution${query ? `?${query}` : ''}`
}

/** 응답을 화면이 믿고 그릴 수 있는 모양으로만 받는다. 모양이 다르면 null — 분포 없이 고른다. */
export function readLayoutDistribution(value: unknown): SiteLayoutDistribution | null {
  if (!value || typeof value !== 'object') return null
  const row = value as { province?: unknown; counts?: unknown; total?: unknown }
  if (row.province !== null && typeof row.province !== 'string') return null
  if (typeof row.total !== 'number' || !row.counts || typeof row.counts !== 'object') return null
  const raw = row.counts as Record<string, unknown>
  const counts = { A: 0, B: 0, C: 0, unset: 0 }
  for (const key of ['A', 'B', 'C', 'unset'] as const) {
    const count = raw[key]
    if (typeof count !== 'number' || !Number.isFinite(count) || count < 0) return null
    counts[key] = count
  }
  return { province: row.province, counts, total: row.total }
}

const COPY = ADMIN_COPY.siteLayout

/** 분포 위 한 줄. 시·도를 모르면 그렇다고 말하고, 다른 공개 병원이 없으면 그렇다고 말한다. */
export function layoutDistributionLine(distribution: SiteLayoutDistribution): string {
  if (!distribution.province) return COPY.distributionUnknown
  if (distribution.total === 0) return COPY.distributionNone.replace('{province}', distribution.province)
  const parts = SITE_LAYOUTS.map((layout) => `${layout} ${distribution.counts[layout]}`)
  if (distribution.counts.unset > 0) parts.push(`${COPY.distributionUnset} ${distribution.counts.unset}`)
  return `${COPY.distribution
    .replace('{province}', distribution.province)
    .replace('{total}', String(distribution.total))} — ${parts.join(' · ')}`
}

/** 카드 하나의 '같은 지역 N곳 사용 중'. 0곳이거나 시·도를 모르면 null — 아무것도 붙이지 않는다. */
export function sameRegionBadge(
  distribution: SiteLayoutDistribution | null,
  layout: SiteLayout,
): string | null {
  if (!distribution?.province) return null
  const count = distribution.counts[layout]
  if (count <= 0) return null
  return COPY.sameRegionCount.replace('{province}', distribution.province).replace('{count}', String(count))
}
