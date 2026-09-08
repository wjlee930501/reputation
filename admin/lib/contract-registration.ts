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

export type ContractRegistrationFailure = {
  /** 운영자가 읽을 한 줄. 무엇이 막혔고 무엇을 고치면 되는지만 말한다. */
  readonly message: string
  /** 이미 그 계약의 병원이 있으면 그 병원 ID. 없으면 null. */
  readonly hospitalId: string | null
}

/** 서버가 문장을 주지 않은 코드만 화면이 채운다. 원인마다 할 일이 다르다. */
const FAILURE_MESSAGES: Record<string, string> = {
  HOSPITAL_EXISTS: '이미 등록된 병원입니다.',
  LEAD_ALREADY_CONVERTED: '이미 병원으로 전환된 상담 요청입니다.',
  CONTRACT_REFERENCE_EXISTS: '이미 사용된 계약 번호입니다. 계약 번호를 계약서의 번호로 고쳐 주세요.',
  ACTIVE_OWNER_REQUIRED: '선택한 담당자를 쓸 수 없습니다. 담당 AE와 영업 담당을 다시 선택해 주세요.',
  HANDOFF_NOT_ASSIGNED: '담당 AE 본인만 인수할 수 있습니다. 담당 AE를 본인으로 두거나 소유자에게 등록을 요청하세요.',
  LEAD_NOT_FOUND: '연결한 상담 요청을 찾을 수 없습니다. 상담 요청 연결 없이 등록해 주세요.',
  VERIFIED_ACTOR_REQUIRED: '관리 화면을 새로고침한 뒤 다시 시도해 주세요.',
  ACTOR_ASSERTION_REQUIRED: '관리 화면을 새로고침한 뒤 다시 시도해 주세요.',
}

/** 기존 병원을 여는 것이 유일한 다음 행동인 코드. 재시도할 것이 없다. */
const OPENS_EXISTING_HOSPITAL = new Set(['HOSPITAL_EXISTS', 'LEAD_ALREADY_CONVERTED'])

const UNKNOWN_FAILURE = '입력한 계약 정보를 확인해 주세요.'

/** 계약 등록 실패 응답을 화면이 그대로 그릴 수 있는 한 줄과 링크로 옮긴다. */
export function registrationFailure(detail: unknown): ContractRegistrationFailure {
  const row = (detail && typeof detail === 'object' ? detail : {}) as {
    code?: unknown
    message?: unknown
    hospital_id?: unknown
  }
  const code = typeof row.code === 'string' ? row.code : ''
  const known: string | undefined = FAILURE_MESSAGES[code]
  const message = typeof row.message === 'string' && row.message ? row.message : known
  return {
    message: message ?? UNKNOWN_FAILURE,
    hospitalId:
      OPENS_EXISTING_HOSPITAL.has(code) && typeof row.hospital_id === 'string'
        ? row.hospital_id
        : null,
  }
}
