import type { Handoff, PlanCode } from '../types/index.ts'

export type AcquisitionInput = {
  readonly salesOwnerId: string
  readonly aeOwnerId: string
  readonly contractReference: string
  readonly contractEffectiveAt: string
  readonly slaDueAt: string
  readonly plan: PlanCode
}

export type AcceptanceDecisionInput = {
  readonly actorId: string
  readonly actorRole: string
  readonly aeOwnerId: string
  readonly reason: string
}

export type AcceptanceDecision =
  | { readonly kind: 'ready'; readonly reason?: string }
  | { readonly kind: 'blocked'; readonly message: string }

export function parsePlanCode(value: string): PlanCode {
  switch (value) {
    case 'PLAN_12': return value
    case 'PLAN_16': return value
    case 'PLAN_20': return value
    default: throw new TypeError('Unknown plan code')
  }
}

export function acceptanceDecision(input: AcceptanceDecisionInput): AcceptanceDecision {
  const reason = input.reason.trim()
  if (input.actorRole === 'OPERATOR' && input.actorId !== input.aeOwnerId) {
    return { kind: 'blocked', message: '담당 AE 본인만 고객 인수를 승인할 수 있습니다.' }
  }
  if (input.actorRole === 'OWNER' && input.actorId !== input.aeOwnerId && !reason) {
    return { kind: 'blocked', message: '다른 AE 대신 인수 승인하는 사유를 입력해 주세요.' }
  }
  if (input.actorRole === 'OWNER' && input.actorId !== input.aeOwnerId) {
    return { kind: 'ready', reason }
  }
  return { kind: 'ready' }
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
