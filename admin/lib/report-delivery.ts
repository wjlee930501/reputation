import type { ReportEvent } from './report-review'

export interface ReportDeliveryInput {
  artifact_sha256: string
  recipient_label: string
  channel: string
  note?: string
}

export interface ReportDeliveryCorrectionInput extends ReportDeliveryInput {
  reason: string
}

export interface ReportDeliveryRescindInput {
  reason: string
}

export type DeliveryIssue = {
  title: string
  problem: string
  customerImpact: string
  nextAction: string
  action: 'refresh' | 'operations' | 'developer'
}

type DeliveryContract = {
  deliveryReady: boolean
  deliveryBlockers: readonly string[]
  effectiveEventType: string | null
  sentAt: string | null
  /** 월간 전달 기록 파이프라인 대상인지. 없으면 월간으로 본다(report-review.ts 참조). */
  deliveryTracked?: boolean
}

function isDeliveryTracked(report: Pick<DeliveryContract, 'deliveryTracked'>): boolean {
  return report.deliveryTracked !== false
}

export function reportSummaryCounts(reports: readonly DeliveryContract[]): {
  delivered: number
  ready: number
  blocked: number
} {
  return reports.reduce((counts, report) => {
    if (isDeliveryTracked(report) && isEffectivelyDelivered(report)) counts.delivered += 1
    else if (report.deliveryReady) counts.ready += 1
    else counts.blocked += 1
    return counts
  }, { delivered: 0, ready: 0, blocked: 0 })
}

/**
 * 목록 행과 요약 카드가 같은 말을 하도록 상태 문구를 한곳에서 만든다.
 *
 * 초기 진단(V0)은 전달 기록을 남기지 않으므로 월간의 "전달 전 검수 가능"으로 부르면
 * 있지도 않은 전달 단계를 가리킨다. 준비 완료 사실만 말한다.
 */
export function reportStatusLabel(report: DeliveryContract): string {
  if (!isDeliveryTracked(report)) {
    return report.deliveryReady ? '원장 보고 자료 준비 완료' : '조치 필요'
  }
  if (isEffectivelyDelivered(report)) return '전달 기록 있음'
  return report.deliveryReady ? '전달 전 검수 가능' : '조치 필요'
}

/** 상태 행에 문제·영향·다음 행동 설명을 붙여야 하는지. */
export function shouldShowDeliveryProblem(report: DeliveryContract): boolean {
  if (report.deliveryReady) return false
  return !(isDeliveryTracked(report) && isEffectivelyDelivered(report))
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

export function readReportDeliveryState(report: Pick<DeliveryContract, 'deliveryReady' | 'deliveryBlockers'>): {
  ready: boolean
  blockers: readonly string[]
} {
  if (report.deliveryReady && report.deliveryBlockers.length === 0) {
    return { ready: true, blockers: [] }
  }
  return {
    ready: false,
    blockers: report.deliveryBlockers.length
      ? report.deliveryBlockers
      : ['최신 전달 가능 상태를 확인할 수 없습니다. ‘최신 상태 다시 확인’을 눌러 주세요.'],
  }
}

export function isEffectivelyDelivered(report: Pick<DeliveryContract, 'effectiveEventType' | 'sentAt'>): boolean {
  if (report.effectiveEventType) return report.effectiveEventType !== 'RESCINDED'
  return Boolean(report.sentAt)
}

export function isDoctorDownloadAvailable(
  report: Pick<DeliveryContract, 'deliveryReady' | 'effectiveEventType' | 'sentAt'>,
): boolean {
  return report.deliveryReady || isEffectivelyDelivered(report)
}

/** API가 생성 시각 오름차순으로 반환한 전달 이력에서 현재 기록을 고른다. */
export function latestDeliveryEvent(events: readonly ReportEvent[]): ReportEvent | undefined {
  return events.at(-1)
}

export function getDoctorDownload(
  hospitalId: string,
  reportId: string,
  artifactState: string,
  report: Pick<DeliveryContract, 'deliveryReady' | 'effectiveEventType' | 'sentAt'>,
): string | null {
  if (artifactState !== 'VALID' || !isDoctorDownloadAvailable(report)) return null
  return `/api/admin/hospitals/${hospitalId}/reports/${reportId}/download?audience=doctor`
}

export function getInternalReportLabel(hasLink: boolean, hasPdf: boolean): string {
  if (hasLink) return '내부 검수용 리포트 열기 · 원장 전달 금지'
  if (hasPdf) return '내부 검수용 링크 준비 중 · 원장 전달 금지'
  return '내부 검수용 리포트 생성 중 · 원장 전달 금지'
}

export function deliveryEventLabel(value: string): string {
  const labels: Record<string, string> = {
    DELIVERED: '최초 전달 기록',
    CORRECTED: '전달 정보 수정 기록',
    RESCINDED: '전달 기록 무효 처리',
    REDELIVERED: '다시 전달한 기록',
  }
  return labels[value] ?? '전달 이력 확인 필요'
}

export function deliveryConflict(detail: unknown): DeliveryIssue {
  const root = record(detail)
  const code = typeof root?.code === 'string' ? root.code : ''
  const blockers = Array.isArray(root?.blockers)
    ? root.blockers.filter((item): item is string => typeof item === 'string')
    : []
  const serverProblem = blockers[0]
  if (code === 'already_delivered') {
    return {
      title: '이미 전달 기록이 있습니다',
      problem: '다른 화면에서 이 리포트의 전달 기록을 먼저 남겼습니다.',
      customerImpact: '같은 전달을 두 번 기록하면 고객 보고 이력이 부정확해집니다.',
      nextAction: '최신 상태를 다시 불러와 전달 이력을 확인해 주세요.',
      action: 'refresh',
    }
  }
  if (code === 'artifact_mismatch' || code === 'doctor_artifact_missing' || code === 'doctor_artifact_invalid') {
    return {
      title: '원장 전달용 파일이 바뀌었습니다',
      problem: serverProblem ?? '화면에서 확인한 파일과 서버의 최신 검증본이 일치하지 않습니다.',
      customerImpact: '이전 파일을 보내면 최신 검증 내용과 다른 자료가 전달될 수 있습니다.',
      nextAction: '최신 상태를 다시 불러온 뒤 원장 전달용 파일을 다시 열어 확인해 주세요.',
      action: 'refresh',
    }
  }
  if (code === 'coverage_incomplete' || code === 'manifest_mismatch' || code === 'manifest_open' || code === 'report_blocked' || code === 'current_readiness_blocked') {
    return {
      title: '최신 병원 자료 또는 측정 확인이 필요합니다',
      problem: serverProblem ?? '전달 직전 확인에서 필수 자료가 준비되지 않은 상태로 바뀌었습니다.',
      customerImpact: '현재 리포트는 원장님께 전달할 수 없습니다.',
      nextAction: '운영 센터에서 차단 사유를 해결한 뒤 최신 리포트를 다시 확인해 주세요.',
      action: 'operations',
    }
  }
  if (code === 'delivery_not_effective') {
    return {
      title: '수정할 유효한 전달 기록이 없습니다',
      problem: '다른 화면에서 전달 기록을 무효 처리했거나 최신 상태가 바뀌었습니다.',
      customerImpact: '없는 기록을 수정하면 고객 보고 이력이 잘못 남습니다.',
      nextAction: '최신 상태를 다시 불러와 현재 전달 이력을 확인해 주세요.',
      action: 'refresh',
    }
  }
  return {
    title: '전달 기록을 처리하지 못했습니다',
    problem: serverProblem ?? '서버의 최신 상태와 이 화면의 상태가 일치하지 않습니다.',
    customerImpact: '전달 기록은 남지 않았습니다.',
    nextAction: '최신 상태를 다시 확인하세요. 계속 실패하면 개발팀 문의용 정보를 복사해 전달해 주세요.',
    action: 'developer',
  }
}

export function deliveryDeveloperNote(hospitalId: string, reportId: string, periodLabel: string): string {
  return [
    '월간 리포트 전달 기록 확인 요청',
    `병원 ID: ${hospitalId}`,
    `리포트 ID: ${reportId}`,
    `대상 기간: ${periodLabel}`,
    `확인 시각: ${new Date().toISOString()}`,
  ].join('\n')
}

export function reportListDeveloperNote(hospitalId: string, checkedAt = new Date()): string {
  return [
    '월간 리포트 목록 확인 요청',
    `병원 ID: ${hospitalId}`,
    `확인 시각: ${checkedAt.toISOString()}`,
  ].join('\n')
}
