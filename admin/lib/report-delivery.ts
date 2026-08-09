export interface ReportDeliveryContract {
  delivery_ready?: boolean
  delivery_blockers?: string[]
}

export interface CustomerReportContract {
  hospital_id: string
  id: string
  doctor_artifact_state?: 'MISSING' | 'INVALID' | 'VALID'
  delivery_ready?: boolean
  download_url?: string | null
}

export interface ReportDeliveryInput {
  artifact_sha256: string
  recipient_label: string
  channel: string
  note?: string
}

export interface EffectiveDeliveryContract {
  sent_at?: string | null
  effective_delivery?: { event_type?: string | null } | null
}

export interface InternalReportContract {
  download_url?: string | null
  has_pdf?: boolean
}

export function getInternalReportLabel(report: InternalReportContract): string {
  if (report.download_url) return 'AE 내부 리포트 다운로드 · 고객 전달 금지'
  if (report.has_pdf) return 'AE 내부 리포트 링크 준비 중 · 고객 전달 금지'
  return 'AE 내부 리포트 생성 중 · 고객 전달 금지'
}

export function getCustomerReportDownload(report: CustomerReportContract): string | null {
  if (report.doctor_artifact_state !== 'VALID' || report.delivery_ready !== true) return null
  return `/api/admin/hospitals/${report.hospital_id}/reports/${report.id}/download?audience=doctor`
}

export function isEffectivelyDelivered(report: EffectiveDeliveryContract): boolean {
  const eventType = report.effective_delivery?.event_type
  if (eventType) return eventType !== 'RESCINDED'
  return Boolean(report.sent_at)
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
