/**
 * 주간 AI 언급률 추이 요약.
 *
 * 백엔드(`/admin/hospitals/{id}/sov/trend`)는 성공 측정이 0건인 주의 sov_pct를 null로 준다.
 * null은 '측정 안 됨'이고 0은 '측정했으나 언급 없음'이라 원장 보고에서 의미가 완전히 다르다.
 * 이 둘을 화면 문구에서 섞지 않도록 상태 판정을 한 곳에 모아 둔다.
 */

export interface SovTrendPoint {
  sov_pct: number | null
}

export type SovTrendState =
  /** 이번 주 측정값이 있다 */
  | 'MEASURED'
  /** 과거 측정 이력은 있지만 이번 주만 비어 있다 */
  | 'NOT_MEASURED_THIS_WEEK'
  /** 아직 성공한 측정이 한 번도 없다 */
  | 'NEVER_MEASURED'

export interface SovTrendSummary {
  current: number | null
  /** 직전 주 대비 %p — 양쪽 주가 모두 측정됐을 때만 산출한다 */
  change: number | null
  state: SovTrendState
  hint: string
}

export function summarizeSovTrend(points: SovTrendPoint[]): SovTrendSummary {
  const rows = Array.isArray(points) ? points : []
  const current = rows.length > 0 ? rows[rows.length - 1].sov_pct ?? null : null
  const previous = rows.length > 1 ? rows[rows.length - 2].sov_pct ?? null : null

  const state: SovTrendState =
    current !== null
      ? 'MEASURED'
      : rows.some((row) => row.sov_pct !== null)
        ? 'NOT_MEASURED_THIS_WEEK'
        : 'NEVER_MEASURED'

  // 결측 주를 0으로 대체해 델타를 만들면 없는 하락/상승을 보고하게 된다 — 비교 불가로 둔다.
  const change = current !== null && previous !== null ? current - previous : null

  return { current, change, state, hint: buildHint(state, change) }
}

function buildHint(state: SovTrendState, change: number | null): string {
  if (change !== null) {
    return `전주 대비 ${change > 0 ? '+' : ''}${change.toFixed(1)}%p`
  }
  if (state === 'NEVER_MEASURED') return '아직 측정 전'
  if (state === 'NOT_MEASURED_THIS_WEEK') return '이번 주 측정 없음'
  return '전주 측정 없음 — 추세 비교 불가'
}
