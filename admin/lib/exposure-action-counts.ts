/**
 * AI 노출 보완 작업 개수의 단일 출처.
 *
 * 대시보드는 `?limit=5`로 받은 목록에서 OPEN+IN_PROGRESS를 세어 "진행 작업"이라
 * 불렀고, 보완 작업 화면은 `?limit=20`으로 받은 목록에서 OPEN만 세어 "대기"라
 * 불렀다. 그래서 같은 병원의 같은 작업 큐가 화면마다 다른 숫자였고, 어느 쪽도
 * 5건·20건을 넘는 병원의 실제 총량은 아니었다.
 *
 * 두 화면이 같은 한계(limit)로 받아 같은 함수로 세고, 한계에 닿았을 때는 숫자가
 * 최소값이라는 사실까지 함께 말한다.
 */

/** 두 화면이 같은 창을 보게 하는 조회 한계 — 백엔드 `limit` 상한과 같다. */
export const EXPOSURE_ACTION_LIST_LIMIT = 20

export interface ExposureActionStatusLike {
  status: string
}

export interface ExposureActionCountSummary {
  waiting: number
  inProgress: number
  blocked: number
  /** 아직 끝나지 않은 작업 — 대기 + 진행중 + 확인필요 */
  active: number
  /** 조회한 목록의 길이 */
  loaded: number
  /** 한계까지 채워져 실제 총량이 더 많을 수 있다 */
  truncated: boolean
}

export function summarizeExposureActions(
  actions: ExposureActionStatusLike[],
  limit: number = EXPOSURE_ACTION_LIST_LIMIT,
): ExposureActionCountSummary {
  const rows = Array.isArray(actions) ? actions : []
  const waiting = rows.filter((action) => action.status === 'OPEN').length
  const inProgress = rows.filter((action) => action.status === 'IN_PROGRESS').length
  const blocked = rows.filter((action) => action.status === 'BLOCKED').length

  return {
    waiting,
    inProgress,
    blocked,
    active: waiting + inProgress + blocked,
    loaded: rows.length,
    truncated: rows.length >= limit,
  }
}

/** 두 화면의 요약 문구 — 같은 숫자를 같은 이름으로 부른다. */
export function describeExposureActions(summary: ExposureActionCountSummary): string {
  if (summary.loaded === 0) return '진단된 보완 작업이 없습니다'
  const parts = [`대기 ${summary.waiting}건`, `진행중 ${summary.inProgress}건`]
  if (summary.blocked > 0) parts.push(`확인필요 ${summary.blocked}건`)
  const text = parts.join(' · ')
  return summary.truncated ? `${text} (상위 ${summary.loaded}건 기준)` : text
}
