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
