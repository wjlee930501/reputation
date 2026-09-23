import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EMPTY_MANUAL_DIAGNOSIS,
  inputContainsClinicName,
  parseKeywords,
  manualDiagnosisRefusal,
  toManualDiagnosisPayload,
  validateManualDiagnosis,
  type ManualDiagnosisValues,
} from './manual-diagnosis.ts'

function filled(overrides: Partial<ManualDiagnosisValues> = {}): ManualDiagnosisValues {
  return {
    clinicName: '장편한외과의원',
    specialty: '외과',
    regionKeyword: '수서역',
    coreKeywords: '대장내시경, 치질',
    contact: '02-123-4567',
    contactName: '홍길동',
    reason: '원장이 진료과를 잘못 적어 다시 만듦',
    ...overrides,
  }
}

test('a complete manual diagnosis passes', () => {
  assert.deepEqual(validateManualDiagnosis(filled()), {})
})

test('an empty form names every missing field', () => {
  const errors = validateManualDiagnosis(EMPTY_MANUAL_DIAGNOSIS)
  for (const field of [
    'clinicName',
    'specialty',
    'regionKeyword',
    'coreKeywords',
    'contact',
    'reason',
  ] as const) {
    assert.ok(errors[field], `${field} 오류가 없습니다.`)
  }
})

test('the reason is required because it is what the audit log keeps', () => {
  // 왜 사람이 직접 만들었는지가 없으면 갈음 기록이 "누군가 바꿨다"로만 남는다.
  assert.ok(validateManualDiagnosis(filled({ reason: '음' })).reason)
  assert.equal(validateManualDiagnosis(filled({ reason: '값 오기' })).reason, undefined)
})

test('the clinic name cannot hide in the measured inputs', () => {
  for (const field of ['specialty', 'regionKeyword', 'coreKeywords'] as const) {
    const errors = validateManualDiagnosis(
      filled({ [field]: '장편한외과의원' } as Partial<ManualDiagnosisValues>),
    )
    assert.match(
      errors.coreKeywords ?? '',
      /병원명을 넣을 수 없습니다/,
      `${field}에 병원명이 들어갔는데 통과했습니다.`,
    )
  }
})

test('spacing tricks do not slip the clinic name through', () => {
  assert.equal(inputContainsClinicName('장편한외과의원', ['장편한 외과 의원 대장내시경']), true)
  assert.equal(inputContainsClinicName('장편한외과의원', ['대장내시경']), false)
  // 병원명이 비어 있으면 판정할 것이 없다.
  assert.equal(inputContainsClinicName('', ['무엇이든']), false)
})

test('the contact is optional when an existing lead already carries one', () => {
  assert.ok(validateManualDiagnosis(filled({ contact: '' })).contact)
  assert.equal(
    validateManualDiagnosis(filled({ contact: '' }), { requireContact: false }).contact,
    undefined,
  )
})

test('keyword parsing matches the backend clean_keywords contract', () => {
  assert.deepEqual(parseKeywords(' 치질 , 치질, 탈장 , , 맹장, 담석, 여섯 '), [
    '치질',
    '탈장',
    '맹장',
    '담석',
  ])
  assert.deepEqual(parseKeywords('   '), [])
})

test('the payload omits blank optionals instead of sending empty strings', () => {
  const payload = toManualDiagnosisPayload(filled({ contactName: '  ' }))
  assert.equal('contact_name' in payload, false)
  assert.equal('lead_id' in payload, false)
  assert.deepEqual(payload.core_keywords, ['대장내시경', '치질'])
  assert.equal(payload.clinic_name, '장편한외과의원')
})

test('the payload carries the lead when an existing inquiry is being corrected', () => {
  const payload = toManualDiagnosisPayload(filled(), 'lead-1')
  assert.equal(payload.lead_id, 'lead-1')
  assert.equal(payload.reason, '원장이 진료과를 잘못 적어 다시 만듦')
})

test('a correction does not send a contact, so the director’s own number is never overwritten', () => {
  const payload = toManualDiagnosisPayload(filled({ contact: '02-000-0000' }), 'lead-1')
  assert.equal('contact' in payload, false)
  assert.equal(toManualDiagnosisPayload(filled()).contact !== undefined, true)
})

test('server refusals the operator can act on are shown instead of a generic message', () => {
  // 갈음 거절은 객체로 온다(inquiry_diagnosis.InquiryDiagnosisError).
  assert.equal(
    manualDiagnosisRefusal(
      { message: '고객 발송 이력이 있는 진단은 갈음할 수 없습니다.', diagnosis_id: 'd-1' },
      409,
      '이미 처리 중이거나 충돌이 발생했습니다.',
    ),
    '고객 발송 이력이 있는 진단은 갈음할 수 없습니다.',
  )
  assert.equal(manualDiagnosisRefusal('병원명을 넣을 수 없습니다.', 400, ''), '병원명을 넣을 수 없습니다.')
  // 입력 검증 배열은 api.ts가 이미 읽을 수 있는 문장으로 합쳐 둔다.
  assert.equal(
    manualDiagnosisRefusal([{ msg: 'x' }], 422, '핵심 키워드는 50자 이내로 입력해 주세요.'),
    '핵심 키워드는 50자 이내로 입력해 주세요.',
  )
  assert.equal(manualDiagnosisRefusal(null, 500, '서버 오류'), null)
})
