import {
  deliveryConflict,
  isEffectivelyDelivered,
  type DeliveryIssue,
} from './report-delivery.ts'

export type DialogKeyDecision = 'close' | 'first' | 'last' | 'native'

export function dialogKeyDecision(
  key: string,
  shiftKey: boolean,
  activeAtFirst: boolean,
  activeAtLast: boolean,
): DialogKeyDecision {
  if (key === 'Escape') return 'close'
  if (key !== 'Tab') return 'native'
  if (shiftKey && activeAtFirst) return 'last'
  if (!shiftKey && activeAtLast) return 'first'
  return 'native'
}

type FreshReportState = {
  readonly deliveryReady: boolean
  readonly deliveryBlockers: readonly string[]
  readonly doctorArtifact: { readonly sha256: string | null }
  readonly effectiveEventType: string | null
  readonly sentAt: string | null
}

export function preflightDeliveryAction(
  report: FreshReportState,
  kind: 'deliver' | 'correct' | 'rescind',
): DeliveryIssue | null {
  if (kind !== 'rescind' && (!report.deliveryReady || !report.doctorArtifact.sha256)) {
    return deliveryConflict({ code: 'report_blocked', blockers: report.deliveryBlockers })
  }
  if (kind === 'deliver' && isEffectivelyDelivered(report)) {
    return deliveryConflict({ code: 'already_delivered' })
  }
  return null
}

/**
 * 닫기 경로(우상단 X·Esc·바깥 클릭·패널 '닫기')가 공유하는 한 규칙 — 저장 안 한 수정이 있으면 확인을 받아야 닫는다.
 * `confirm`은 확인 창을 띄우는 함수(브라우저 `window.confirm`)라 수정이 없을 때는 부르지 않는다.
 */
export function dialogCloseAllowed(dirty: boolean, confirm: () => boolean): boolean {
  return !dirty || confirm()
}
