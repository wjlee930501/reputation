import type { Handoff, PlanCode } from '../types/index.ts'

export type AcquisitionInput = {
  readonly salesOwnerId: string
  readonly aeOwnerId: string
  readonly contractReference: string
  readonly contractEffectiveAt: string
  readonly slaDueAt: string
  readonly plan: PlanCode
}

export function contractPayload(input: AcquisitionInput, version: number) {
  return {
    version,
    contract_reference: input.contractReference.trim(),
    contract_effective_at: input.contractEffectiveAt,
    plan: input.plan,
    sla_due_at: input.slaDueAt,
  }
}

export function acceptancePayload(version: number, reason?: string) {
  return reason?.trim() ? { version, reason: reason.trim() } : { version }
}

export function handoffNextAction(handoff: Handoff): string {
  switch (handoff.state) {
    case 'CONTRACT_PENDING': return '계약 정보 입력'
    case 'CONTRACTED': return 'AE 고객 인수 승인'
    case 'HANDOFF_ACCEPTED': return '병원 프로파일 입력'
  }
}
