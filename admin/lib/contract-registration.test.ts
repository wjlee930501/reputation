import assert from 'node:assert/strict'
import test from 'node:test'

import {
  registrationFailure,
  registrationBlockReason,
  registrationPayload,
  suggestContractReference,
  todayInKorea,
  type ContractRegistrationForm,
} from './contract-registration.ts'

function form(overrides: Partial<ContractRegistrationForm> = {}): ContractRegistrationForm {
  return {
    name: ' 장편한외과의원 ',
    leadId: 'lead-1',
    contractReference: ' RP-202609-a1b2 ',
    effectiveDate: '2026-09-09',
    plan: 'PLAN_16',
    aeOwnerId: 'ae-1',
    salesOwnerId: 'sales-1',
    ...overrides,
  }
}

test('the suggested contract reference carries the Korean year-month and a hex suffix', () => {
  // 2026-09-01 00:30 KST = 2026-08-31 15:30 UTC — 제안값은 한국 달을 따라야 한다.
  const reference = suggestContractReference(new Date('2026-08-31T15:30:00Z'), () => 0.99)
  assert.equal(reference, 'RP-202609-ffff')
  assert.match(suggestContractReference(new Date('2026-09-09T00:00:00Z'), () => 0), /^RP-202609-0000$/)
})

test('the default effective date is today in Korea', () => {
  assert.equal(todayInKorea(new Date('2026-08-31T15:30:00Z')), '2026-09-01')
  assert.equal(todayInKorea(new Date('2026-09-09T14:59:00Z')), '2026-09-09')
})

test('the payload trims operator input and sends empty owners as null', () => {
  assert.deepEqual(registrationPayload(form()), {
    name: '장편한외과의원',
    lead_id: 'lead-1',
    contract_reference: 'RP-202609-a1b2',
    contract_effective_at: '2026-09-09',
    plan: 'PLAN_16',
    ae_owner_id: 'ae-1',
    sales_owner_id: 'sales-1',
  })
  const walkIn = registrationPayload(form({ leadId: null, salesOwnerId: '' }))
  assert.equal(walkIn.lead_id, null)
  assert.equal(walkIn.sales_owner_id, null)
})

test('the block reason names the one field that is still missing', () => {
  assert.equal(registrationBlockReason(form()), null)
  assert.match(registrationBlockReason(form({ name: '  ' })) ?? '', /병원명/)
  assert.match(registrationBlockReason(form({ contractReference: '' })) ?? '', /계약 번호/)
  assert.match(registrationBlockReason(form({ effectiveDate: '' })) ?? '', /효력일/)
  assert.match(registrationBlockReason(form({ aeOwnerId: '' })) ?? '', /담당 AE/)
})

test('only the two "already exists" codes offer the existing hospital to open', () => {
  assert.equal(registrationFailure({ code: 'HOSPITAL_EXISTS', hospital_id: 'h-1' }).hospitalId, 'h-1')
  assert.equal(
    registrationFailure({ code: 'LEAD_ALREADY_CONVERTED', hospital_id: 'h-2' }).hospitalId,
    'h-2',
  )
  assert.equal(registrationFailure({ code: 'HOSPITAL_EXISTS' }).hospitalId, null)
  // 계약 번호 중복은 그 병원을 여는 문제가 아니라 이 화면에서 번호를 고치는 문제다.
  assert.equal(
    registrationFailure({ code: 'CONTRACT_REFERENCE_EXISTS', hospital_id: 'h-1' }).hospitalId,
    null,
  )
  assert.equal(registrationFailure('nope').hospitalId, null)
  assert.equal(registrationFailure(null).hospitalId, null)
})

test('each failure code gets its own line naming what to fix', () => {
  // 서버 문장이 있으면 그것이 정본이다.
  assert.equal(
    registrationFailure({ code: 'CONTRACT_REFERENCE_EXISTS', message: '이미 사용된 계약 번호입니다.' })
      .message,
    '이미 사용된 계약 번호입니다.',
  )
  assert.match(registrationFailure({ code: 'CONTRACT_REFERENCE_EXISTS' }).message, /계약 번호/)
  assert.match(registrationFailure({ code: 'ACTIVE_OWNER_REQUIRED' }).message, /담당 AE/)
  assert.match(registrationFailure({ code: 'HANDOFF_NOT_ASSIGNED' }).message, /담당 AE 본인/)
  assert.match(registrationFailure({ code: 'LEAD_NOT_FOUND' }).message, /상담 요청/)
  assert.match(registrationFailure({ code: 'LEAD_ALREADY_CONVERTED' }).message, /전환된 상담 요청/)
  assert.match(registrationFailure({ code: 'VERIFIED_ACTOR_REQUIRED' }).message, /새로고침/)
  assert.match(registrationFailure({ code: 'ACTOR_ASSERTION_REQUIRED' }).message, /새로고침/)
  const codes = [
    'HOSPITAL_EXISTS',
    'LEAD_ALREADY_CONVERTED',
    'CONTRACT_REFERENCE_EXISTS',
    'ACTIVE_OWNER_REQUIRED',
    'HANDOFF_NOT_ASSIGNED',
    'LEAD_NOT_FOUND',
    'VERIFIED_ACTOR_REQUIRED',
  ]
  const lines = codes.map((code) => registrationFailure({ code }).message)
  assert.equal(new Set(lines).size, codes.length)
  // 알 수 없는 실패만 예전의 뭉뚱그린 한 줄로 떨어진다.
  assert.match(registrationFailure({ code: 'NOPE' }).message, /계약 정보를 확인/)
  assert.match(registrationFailure(undefined).message, /계약 정보를 확인/)
})
