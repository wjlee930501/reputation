import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EMPTY_INQUIRY,
  INQUIRY_CLINIC_TYPE,
  toInquiryFormData,
  validateInquiryForm,
  type InquiryFormValues,
} from './inquiry-form.ts'

function filled(overrides: Partial<InquiryFormValues> = {}): InquiryFormValues {
  return {
    clinicName: '장편한외과의원',
    specialty: '외과',
    regionKeyword: '수서역',
    coreKeywords: '대장내시경, 치질',
    contactName: '홍길동',
    contact: '010-1234-5678',
    question: '도입 절차가 궁금합니다.',
    privacy: true,
    ...overrides,
  }
}

test('a complete inquiry passes', () => {
  assert.deepEqual(validateInquiryForm(filled()), {})
})

test('an empty inquiry names every missing field', () => {
  const errors = validateInquiryForm(EMPTY_INQUIRY)
  for (const field of [
    'clinicName',
    'specialty',
    'regionKeyword',
    'coreKeywords',
    'contactName',
    'contact',
    'question',
    'privacy',
  ] as const) {
    assert.ok(errors[field], `${field} 오류가 없습니다.`)
  }
})

test('the diagnosis inputs are required because the report depends on them', () => {
  // 셋 중 하나라도 비면 백엔드가 초도 진단을 만들지 않는다 — 접수만 되고 연락할 근거가 없다.
  for (const field of ['specialty', 'regionKeyword', 'coreKeywords'] as const) {
    const errors = validateInquiryForm(filled({ [field]: '  ' } as Partial<InquiryFormValues>))
    assert.ok(errors[field], `${field}를 비웠는데 통과했습니다.`)
  }
})

test('a keyword containing the clinic name is refused', () => {
  // 병원명이 질의에 들어가면 언급은 보장되고 측정은 무의미해진다.
  const errors = validateInquiryForm(filled({ coreKeywords: '장편한 외과 의원 대장내시경, 치질' }))
  assert.match(errors.coreKeywords ?? '', /병원명을 넣을 수 없습니다/)
})

test('an unreachable phone number is caught before the SMS silently fails', () => {
  assert.ok(validateInquiryForm(filled({ contact: '1234' })).contact)
  assert.equal(validateInquiryForm(filled({ contact: '01012345678' })).contact, undefined)
})

test('patient-sensitive text is refused in every free-text field', () => {
  for (const field of ['clinicName', 'specialty', 'regionKeyword', 'contactName', 'question'] as const) {
    const errors = validateInquiryForm(
      filled({ [field]: '환자 주민등록번호 900101-1234567' } as Partial<InquiryFormValues>),
    )
    assert.match(errors[field] ?? '', /환자 개인정보/, `${field}가 통과했습니다.`)
  }
  assert.match(
    validateInquiryForm(filled({ coreKeywords: '진료 기록 조회' })).coreKeywords ?? '',
    /환자 개인정보/,
  )
})

test('the payload keeps the inquiry marker out of the specialty field', () => {
  // clinic_type을 진료과가 덮으면 Admin에서 도입문의와 무료진단을 구분할 수 없다.
  const form = toInquiryFormData(filled(), '/')
  assert.equal(form.get('clinicType'), INQUIRY_CLINIC_TYPE)
  assert.equal(form.get('specialty'), '외과')
  assert.equal(form.get('regionKeyword'), '수서역')
  assert.equal(form.get('coreKeywords'), '대장내시경, 치질')
  assert.equal(form.get('privacy'), 'on')
  assert.equal(form.get('source_path'), '/')
})

test('the payload trims and de-duplicates keywords like the backend does', () => {
  const form = toInquiryFormData(
    filled({ coreKeywords: ' 치질 , 치질, 탈장 , , 맹장, 담석, 여섯번째 ' }),
    '/',
  )
  assert.equal(form.get('coreKeywords'), '치질, 탈장, 맹장, 담석')
})
