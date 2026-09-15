// 목록 상단에 남는 단 하나의 큐 — 지난달 원장 보고 누락.
//
// 공개 후 확인 표본은 발행을 막지 않는 관측용 표본이고 두 번째 승인 큐가 아니므로
// (`post_publish_review_policy.py`) 어떤 운영자 큐·목록 라벨에도 올리지 않는다. 표본은
// 콘텐츠 탭 필터와 보고서 증빙에만 남는다.

export interface AttentionHospital {
  hospital_id: string
  hospital_name: string
  unreviewed_count: number
  overdue_count: number
  oldest_published_at: string | null
  /** 공개 페이지가 숨기는 중인 발행 글 — 확인이 아니라 보류 해소가 할 일이다(H-01). */
  withheld_count: number
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

/** `/admin/operations/attention` 응답 그대로. 표본 필드는 응답에 남아 있지만 목록은 읽지 않는다. */
export interface AttentionQueue {
  unreviewed_total: number
  overdue_total: number
  overdue_hours: number
  withheld_total: number
  hospitals: AttentionHospital[]
  reports?: AttentionReports
}

/** 목록에 한 번에 보여줄 병원 수. 큐는 훑어보는 것이지 읽는 것이 아니다. */
export const ATTENTION_VISIBLE_ROWS = 5

/** 지난달 원장 보고에 빠진 곳이 있는지. 목록 상단은 이것만 보고 뜬다. */
export function hasReportGaps(queue: AttentionQueue | null): boolean {
  const reports = queue?.reports
  return Boolean(reports && (reports.missing.length > 0 || reports.undelivered.length > 0))
}

/** 원장 보고 줄에 붙일 한 마디. 두 상태를 한 줄로 합치지 않는다 — 할 일이 다르다. */
export function reportGapSummary(reports: AttentionReports): string {
  const parts: string[] = []
  if (reports.missing.length > 0) parts.push(`미생성 ${reports.missing.length}곳`)
  if (reports.undelivered.length > 0) parts.push(`미전달 ${reports.undelivered.length}곳`)
  return parts.join(' · ')
}

/** 목록에 담지 못한 나머지 병원 수. 화면이 조용히 잘라 먹지 않게 남은 수를 말한다. */
export function hiddenReportCount(
  reports: AttentionReports,
  visibleRows: number = ATTENTION_VISIBLE_ROWS,
): number {
  return Math.max(0, reports.missing.length + reports.undelivered.length - visibleRows)
}
