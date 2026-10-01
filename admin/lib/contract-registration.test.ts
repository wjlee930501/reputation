import assert from 'node:assert/strict'
import test from 'node:test'

import {
  registrationFailure,
  registrationBlockReason,
  registrationPayload,
  type ContractRegistrationForm,
} from './contract-registration.ts'

function form(overrides: Partial<ContractRegistrationForm> = {}): ContractRegistrationForm {
  return {
    name: ' 장편한외과의원 ',
    leadId: 'lead-1',
    plan: 'PLAN_16',
    aeOwnerId: 'ae-1',
    ...overrides,
  }
}

test('the payload trims the name and leaves the server-filled contract facts out', () => {
  assert.deepEqual(registrationPayload(form()), {
    name: '장편한외과의원',
    lead_id: 'lead-1',
    plan: 'PLAN_16',
    ae_owner_id: 'ae-1',
  })
  // 계약 번호·효력일·영업 담당은 저장만 되고 아무 동작도 바꾸지 않는다 — 서버가 채운다.
  const payload = registrationPayload(form({ leadId: null })) as Record<string, unknown>
  assert.equal(payload.lead_id, null)
  for (const key of ['contract_reference', 'contract_effective_at', 'sales_owner_id']) {
    assert.ok(!(key in payload), `unexpected field: ${key}`)
  }
})

test('the block reason names the one field that is still missing', () => {
  assert.equal(registrationBlockReason(form()), null)
  assert.match(registrationBlockReason(form({ name: '  ' })) ?? '', /병원명/)
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
    registrationFailure({ code: 'HOSPITAL_EXISTS', message: '서버 문장' }).message,
    '서버 문장',
  )
  // 계약 번호는 화면이 받지 않으므로 "번호를 고쳐라"는 서버 문장 대신 다시 누르라고 한다.
  const collision = registrationFailure({
    code: 'CONTRACT_REFERENCE_EXISTS',
    message: '이미 사용된 계약 번호입니다. 다른 번호를 입력해 주세요.',
  }).message
  assert.match(collision, /한 번 더 눌러/)
  assert.doesNotMatch(collision, /입력/)
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
