export interface ReportDeliveryContract {
  delivery_ready?: boolean
  delivery_blockers?: string[]
}

export function readReportDeliveryState(report: ReportDeliveryContract): {
  ready: boolean
  blockers: string[]
} {
  const blockers = Array.isArray(report.delivery_blockers)
    ? report.delivery_blockers.filter((blocker) => blocker.trim().length > 0)
    : []

  if (report.delivery_ready === true && blockers.length === 0) return { ready: true, blockers: [] }
  return {
    ready: false,
    blockers: blockers.length > 0
      ? blockers
      : ['백엔드 전달 준비 상태를 확인할 수 없습니다. 새로 고침 후 다시 확인해 주세요.'],
  }
}
