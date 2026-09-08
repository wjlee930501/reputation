import type { PlanCode } from '../types/index.ts'

export type ContractRegistrationForm = {
  readonly name: string
  readonly leadId: string | null
  readonly contractReference: string
  readonly effectiveDate: string
  readonly plan: PlanCode
  readonly aeOwnerId: string
  readonly salesOwnerId: string
}

export type ContractRegistrationPayload = {
  readonly name: string
  readonly lead_id: string | null
  readonly contract_reference: string
  readonly contract_effective_at: string
  readonly plan: PlanCode
  readonly ae_owner_id: string
  readonly sales_owner_id: string | null
}

/** 계약 번호 제안값. 운영자가 계약서의 실제 번호로 고칠 수 있는 초안이다.
 *
 * 서버가 계약 번호의 유일성을 판정하므로 여기서는 충돌 확률만 낮추면 된다.
 * 연·월은 운영자가 눈으로 읽는 값이라 한국 날짜 기준으로 만든다.
 */
export function suggestContractReference(
  now: Date = new Date(),
  random: () => number = Math.random,
): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(now)
  const year = parts.find((part) => part.type === 'year')?.value
  const month = parts.find((part) => part.type === 'month')?.value
  if (!year || !month) throw new TypeError('Contract reference requires a formattable date')
  let suffix = ''
  for (let index = 0; index < 4; index += 1) {
    suffix += Math.floor(random() * 16).toString(16)
  }
  return `RP-${year}${month}-${suffix}`
}

/** 오늘(한국 기준) 날짜. 계약 효력일의 기본값. */
export function todayInKorea(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now)
  return parts
}

/** 화면 입력을 `POST /admin/hospitals/register-contract` 본문으로 옮긴다. */
export function registrationPayload(form: ContractRegistrationForm): ContractRegistrationPayload {
  return {
    name: form.name.trim(),
    lead_id: form.leadId || null,
    contract_reference: form.contractReference.trim(),
    contract_effective_at: form.effectiveDate,
    plan: form.plan,
    ae_owner_id: form.aeOwnerId,
    sales_owner_id: form.salesOwnerId || null,
  }
}

/** 등록을 막고 있는 첫 번째 이유. 없으면 null. */
export function registrationBlockReason(form: ContractRegistrationForm): string | null {
  if (!form.name.trim()) return '병원명을 입력해야 등록할 수 있습니다.'
  if (!form.contractReference.trim()) return '계약 번호를 입력해야 등록할 수 있습니다.'
  if (!form.effectiveDate) return '계약 효력일을 입력해야 등록할 수 있습니다.'
  if (!form.aeOwnerId) return '담당 AE를 선택해야 등록할 수 있습니다.'
  return null
}

/** 409 응답에서 기존 병원 ID를 읽는다. 없으면 null. */
export function existingHospitalId(detail: unknown): string | null {
  if (!detail || typeof detail !== 'object') return null
  const row = detail as { code?: unknown; hospital_id?: unknown }
  if (row.code !== 'HOSPITAL_EXISTS') return null
  return typeof row.hospital_id === 'string' ? row.hospital_id : null
}
