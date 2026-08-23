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

export interface SovTrendWeek extends SovTrendPoint {
  /** 그 주 언급률 분모에 들어간 측정 수 — 0이면 그 주에 측정 자체가 없었다 */
  total_count?: number
}

/**
 * 측정이 시작되기 전의 주를 잘라낸다.
 *
 * 백엔드는 언제나 최근 12주를 돌려주므로, 지난주에 계약한 병원도 11주 분량의
 * 빈 칸을 받는다. 그 빈 칸이 x축에 그려지면 측정이 열한 번 실패한 것처럼 읽히고,
 * 그래프의 측정 구간은 오른쪽 끝에 눌려 변화가 보이지 않는다.
 *
 * 첫 측정 이후의 공백은 남긴다 — 측정이 끊긴 주는 실제 운영 신호이므로 지우면
 * 오히려 사실을 가린다.
 */
export function trimTrendToMeasuredWeeks<T extends SovTrendWeek>(points: T[]): T[] {
  const rows = Array.isArray(points) ? points : []
  const firstMeasured = rows.findIndex(
    (row) => row.sov_pct !== null || (row.total_count ?? 0) > 0,
  )
  if (firstMeasured < 0) return []
  return rows.slice(firstMeasured)
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
