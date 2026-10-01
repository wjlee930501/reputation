import type { PlanCode } from '../types/index.ts'

/** 계약 등록 화면이 받는 값. 계약 번호·효력일·영업 담당은 받지 않는다 — 저장만 되고
 * 어떤 동작도 바꾸지 않아 의미 없는 기입이었다. 서버가 채운다(`ContractRegistration`). */
export type ContractRegistrationForm = {
  readonly name: string
  readonly leadId: string | null
  readonly plan: PlanCode
  readonly aeOwnerId: string
}

export type ContractRegistrationPayload = {
  readonly name: string
  readonly lead_id: string | null
  readonly plan: PlanCode
  readonly ae_owner_id: string
}

/** 화면 입력을 `POST /admin/hospitals/register-contract` 본문으로 옮긴다. */
export function registrationPayload(form: ContractRegistrationForm): ContractRegistrationPayload {
  return {
    name: form.name.trim(),
    lead_id: form.leadId || null,
    plan: form.plan,
    ae_owner_id: form.aeOwnerId,
  }
}

/** 등록을 막고 있는 첫 번째 이유. 없으면 null. */
export function registrationBlockReason(form: ContractRegistrationForm): string | null {
  if (!form.name.trim()) return '병원명을 입력해야 등록할 수 있습니다.'
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
  // 계약 번호는 서버가 만든다. 드물게 겹치면 다시 누를 때 새 번호가 나온다.
  CONTRACT_REFERENCE_EXISTS: '계약 번호가 겹쳤습니다. 계약 등록을 한 번 더 눌러 주세요.',
  ACTIVE_OWNER_REQUIRED: '선택한 담당 AE를 쓸 수 없습니다. 담당 AE를 다시 선택해 주세요.',
  HANDOFF_NOT_ASSIGNED: '담당 AE 본인만 인수할 수 있습니다. 담당 AE를 본인으로 두거나 소유자에게 등록을 요청하세요.',
  LEAD_NOT_FOUND: '연결한 상담 요청을 찾을 수 없습니다. 상담 요청 연결 없이 등록해 주세요.',
  VERIFIED_ACTOR_REQUIRED: '관리 화면을 새로고침한 뒤 다시 시도해 주세요.',
  ACTOR_ASSERTION_REQUIRED: '관리 화면을 새로고침한 뒤 다시 시도해 주세요.',
}

/** 기존 병원을 여는 것이 유일한 다음 행동인 코드. 재시도할 것이 없다. */
const OPENS_EXISTING_HOSPITAL = new Set(['HOSPITAL_EXISTS', 'LEAD_ALREADY_CONVERTED'])

/** 화면이 보내지 않은 값을 두고 고치라고 말하는 서버 문장은 버리고 화면 문장을 쓴다. */
const SCREEN_OWNS_MESSAGE = new Set(['CONTRACT_REFERENCE_EXISTS'])

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
  const serverMessage =
    typeof row.message === 'string' && row.message && !SCREEN_OWNS_MESSAGE.has(code)
      ? row.message
      : undefined
  return {
    message: serverMessage ?? known ?? UNKNOWN_FAILURE,
    hospitalId:
      OPENS_EXISTING_HOSPITAL.has(code) && typeof row.hospital_id === 'string'
        ? row.hospital_id
        : null,
  }
}
