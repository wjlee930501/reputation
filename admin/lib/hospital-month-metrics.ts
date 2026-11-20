/**
 * 병원 목록·대시보드의 「이번 달 발행」·「AI 언급률」(2026-11-20 마케팅 요청).
 *
 * 값은 `GET /admin/hospitals/month-metrics` — 현황 탭과 같은 서버 함수(`_month_summary`)라 숫자가
 * 같다. 언급률 null은 「측정 전」이지 0%가 아니다.
 */

import { ADMIN_COPY } from './admin-copy.ts'

export interface HospitalMonthMetrics {
  readonly hospital_id: string
  readonly year: number
  readonly month: number
  readonly published_count: number
  readonly public_count: number
  readonly planned_total: number
  readonly mention_rate: number | null
  readonly mention_rate_measured_at: string | null
}

export function metricsByHospital(rows: unknown): ReadonlyMap<string, HospitalMonthMetrics> {
  const map = new Map<string, HospitalMonthMetrics>()
  if (!Array.isArray(rows)) return map
  for (const row of rows) {
    if (row && typeof row === 'object' && typeof (row as HospitalMonthMetrics).hospital_id === 'string') {
      map.set((row as HospitalMonthMetrics).hospital_id, row as HospitalMonthMetrics)
    }
  }
  return map
}

/** 진행 막대 비율(0~1)과 「공개 n / 계획 m」 글자. 계획이 0이면 비율은 0. */
export function publishProgress(metrics: HospitalMonthMetrics | undefined): { ratio: number; label: string } | null {
  if (!metrics) return null
  const planned = Math.max(0, metrics.planned_total)
  const ratio = planned > 0 ? Math.min(1, metrics.public_count / planned) : 0
  return { ratio, label: `${metrics.public_count} / ${planned}` }
}

export function mentionRateLabel(metrics: HospitalMonthMetrics | undefined): string {
  if (!metrics) return '—'
  return metrics.mention_rate === null ? ADMIN_COPY.hospitalList.notMeasured : `${metrics.mention_rate.toFixed(1)}%`
}

export type HospitalSort = 'created' | 'name' | 'publish' | 'mention'

/** 정렬: 이름은 가나다, 발행·언급률은 높은 순(값 없음은 뒤). 같으면 원래 순서. */
export function sortHospitals<T extends { id: string; name: string }>(
  rows: readonly T[],
  sort: HospitalSort,
  metrics: ReadonlyMap<string, HospitalMonthMetrics>,
): T[] {
  if (sort === 'created') return [...rows]
  const value = (row: T): number | null => {
    const m = metrics.get(row.id)
    if (!m) return null
    if (sort === 'publish') return m.planned_total > 0 ? m.public_count / m.planned_total : m.public_count > 0 ? 1 : 0
    return m.mention_rate
  }
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      if (sort === 'name') return a.row.name.localeCompare(b.row.name, 'ko') || a.index - b.index
      const av = value(a.row)
      const bv = value(b.row)
      if (av === null && bv === null) return a.index - b.index
      if (av === null) return 1
      if (bv === null) return -1
      return bv - av || a.index - b.index
    })
    .map((entry) => entry.row)
}
