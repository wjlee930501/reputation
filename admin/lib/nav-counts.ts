/**
 * 메뉴 배지(2026-11-20 마케팅 요청). 회색 = 개수만(경고 아님), 주황 = 처리할 일. 0건은 숨긴다.
 *
 * 처리 목록 배지는 `/admin/operations/nav-counts`의 `operator_todo_groups` — 문제·복구 큐 total과
 * 같은 값(`is_operator_todo`를 통과한 사고의 원인 묶음 수, 일일 요약의 「사람의 일」)이다.
 */

export interface NavCounts {
  readonly operator_todo_groups: number
  readonly hospital_count: number
  readonly lead_total: number
  readonly lead_needs_attention: number
}

export type NavBadgeKey = 'operations' | 'hospitals' | 'leads'

export interface NavBadge {
  readonly value: string
  readonly label: string
  readonly tone: 'attention' | 'count'
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0
}

export function navBadge(key: NavBadgeKey, counts: NavCounts | null): NavBadge | null {
  if (!counts) return null
  const value =
    key === 'operations' ? count(counts.operator_todo_groups)
    : key === 'hospitals' ? count(counts.hospital_count)
    : count(counts.lead_total)
  if (value === 0) return null
  return {
    value: value > 99 ? '99+' : String(value),
    label: key === 'operations' ? `처리할 일 ${value}건` : `${value}건`,
    tone: key === 'operations' ? 'attention' : 'count',
  }
}
