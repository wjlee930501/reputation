import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EMPTY_MANUAL_DIAGNOSIS,
  inputContainsClinicName,
  parseKeywords,
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
