// 공개 후 아직 사람이 확인하지 않은 콘텐츠 큐.
//
// 08:00 자동 발행은 사람 승인 없이 공개되므로, 위험은 "발행 전 승인"이 아니라 공개된 뒤
// 아무도 안 본 시간이다. 지금까지 이 상태는 병원 상세에 들어가야만 보였다.

export interface AttentionHospital {
  hospital_id: string
  hospital_name: string
  unreviewed_count: number
  overdue_count: number
  oldest_published_at: string | null
}

export interface AttentionReportHospital {
  hospital_id: string
  hospital_name: string
  report_id: string | null
}

/** 지난달 원장 보고가 빠진 곳 — 만들어지지 않았거나, 만들어졌는데 안 갔거나. */
export interface AttentionReports {
  period_year: number
  period_month: number
  missing: AttentionReportHospital[]
  undelivered: AttentionReportHospital[]
}

export interface AttentionQueue {
  unreviewed_total: number
  overdue_total: number
  overdue_hours: number
  hospitals: AttentionHospital[]
  reports?: AttentionReports
}

/** 목록에 한 번에 보여줄 병원 수. 큐는 훑어보는 것이지 읽는 것이 아니다. */
export const ATTENTION_VISIBLE_ROWS = 5

/** 공개 시각부터 지금까지를 사람이 읽는 한 마디로. 하루가 넘으면 일 단위로 센다. */
export function formatWaiting(oldestPublishedAt: string | null, now: Date = new Date()): string {
  if (!oldestPublishedAt) return ''
  const published = new Date(oldestPublishedAt)
  const elapsedMs = now.getTime() - published.getTime()
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) return ''

  const hours = Math.floor(elapsedMs / (60 * 60 * 1000))
  if (hours < 1) return '방금'
  if (hours < 24) return `${hours}시간째`
  return `${Math.floor(hours / 24)}일째`
}

/** 지난달 원장 보고에 빠진 곳이 있는지. */
export function hasReportGaps(queue: AttentionQueue | null): boolean {
  const reports = queue?.reports
  return Boolean(reports && (reports.missing.length > 0 || reports.undelivered.length > 0))
}

/** 큐를 띄울지. 확인할 것이 없으면 화면에 아무것도 더하지 않는다. */
export function hasAttentionWork(queue: AttentionQueue | null): boolean {
  if (!queue) return false
  return queue.unreviewed_total > 0 || hasReportGaps(queue)
}

/** 원장 보고 줄에 붙일 한 마디. 두 상태를 한 줄로 합치지 않는다 — 할 일이 다르다. */
export function reportGapSummary(reports: AttentionReports): string {
  const parts: string[] = []
  if (reports.missing.length > 0) parts.push(`미생성 ${reports.missing.length}곳`)
  if (reports.undelivered.length > 0) parts.push(`미전달 ${reports.undelivered.length}곳`)
  return parts.join(' · ')
}

/** 목록에 담지 못한 나머지 병원 수. */
export function hiddenHospitalCount(
  queue: AttentionQueue,
  visibleRows: number = ATTENTION_VISIBLE_ROWS,
): number {
  return Math.max(0, queue.hospitals.length - visibleRows)
}
