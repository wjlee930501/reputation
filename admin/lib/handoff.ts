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

export type AcquisitionDates = {
  readonly effectiveDate: string
  readonly slaDueAt: string
}

export type OnboardingWorkflowCheckpoint = {
  readonly hospitalId: string
  readonly handoffId: string
}

export const ONBOARDING_WORKFLOW_STORAGE_KEY = 'reputation:onboarding-workflow:v1'
export const ONBOARDING_CREATE_REQUEST_STORAGE_KEY = 'reputation:onboarding-create-request:v1'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function datePart(
  parts: Intl.DateTimeFormatPart[],
  type: 'year' | 'month' | 'day' | 'hour' | 'minute',
): number {
  const value = parts.find((part) => part.type === type)?.value
  if (!value) throw new TypeError(`Missing ${type} in formatted date`)
  return Number(value)
}

function calendarDate(year: number, month: number, day: number): string {
  return `${year.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`
}

function koreanDateTimeParts(value: string): Intl.DateTimeFormatPart[] | null {
  const instant = new Date(value)
  if (Number.isNaN(instant.getTime())) return null
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(instant)
}

export function koreanDateInputValue(value: string | null | undefined): string | null {
  if (!value) return null
  const parts = koreanDateTimeParts(value)
  if (!parts) return null
  return calendarDate(datePart(parts, 'year'), datePart(parts, 'month'), datePart(parts, 'day'))
}

export function koreanDateTimeLocalInputValue(value: string | null | undefined): string | null {
  if (!value) return null
  const parts = koreanDateTimeParts(value)
  if (!parts) return null
  const date = calendarDate(datePart(parts, 'year'), datePart(parts, 'month'), datePart(parts, 'day'))
  const hour = datePart(parts, 'hour').toString().padStart(2, '0')
  const minute = datePart(parts, 'minute').toString().padStart(2, '0')
  return `${date}T${hour}:${minute}`
}

/** Contract defaults derived from the current Korean business date. */
export function defaultAcquisitionDates(now: Date = new Date()): AcquisitionDates {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const year = datePart(parts, 'year')
  const month = datePart(parts, 'month')
  const day = datePart(parts, 'day')
  const nextDate = new Date(Date.UTC(year, month - 1, day + 1))

  return {
    effectiveDate: calendarDate(year, month, day),
    slaDueAt: `${calendarDate(
      nextDate.getUTCFullYear(),
      nextDate.getUTCMonth() + 1,
      nextDate.getUTCDate(),
    )}T18:00`,
  }
}

/** Restore only opaque identifiers; clinic/contact details never enter browser storage. */
export function parseOnboardingWorkflowCheckpoint(
  raw: string | null,
): OnboardingWorkflowCheckpoint | null {
  if (!raw) return null
  try {
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object') return null
    const { hospitalId, handoffId } = value as Record<string, unknown>
    if (
      typeof hospitalId !== 'string'
      || typeof handoffId !== 'string'
      || !UUID_PATTERN.test(hospitalId)
      || !UUID_PATTERN.test(handoffId)
    ) return null
    return { hospitalId, handoffId }
  } catch {
    return null
  }
}

export function serializeOnboardingWorkflowCheckpoint(
  checkpoint: OnboardingWorkflowCheckpoint,
): string {
  if (!UUID_PATTERN.test(checkpoint.hospitalId) || !UUID_PATTERN.test(checkpoint.handoffId)) {
    throw new TypeError('Onboarding workflow identifiers must be UUIDs')
  }
  return JSON.stringify(checkpoint)
}

export function parseOnboardingCreateRequestId(raw: string | null): string | null {
  return raw && UUID_PATTERN.test(raw) ? raw : null
}

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
    case 'HANDOFF_ACCEPTED': return '병원 기본 정보 입력'
  }
}
