import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EMPTY_FORM,
  MAX_KEYWORDS,
  confirmationRows,
  isUsableEmail,
  isUsablePhone,
  keywordContainsHospitalName,
  parseKeywords,
  suggestEmailCorrection,
  toRequestPayload,
  validateDiagnosisForm,
} from './diagnosis-form.ts'

const VALID = {
  clinicName: '장편한외과의원',
  clinicType: '외과',
  regionKeyword: '수서역',
  clinicPhone: '02-123-4567',
  coreKeywords: '대장내시경, 치질',
  contactName: '홍길동',
  contact: '010-1234-5678',
  email: 'doctor@example.com',
  privacy: true,
}

// ── 키워드 파싱 ─────────────────────────────────────────────────────
// 첫 번째 키워드가 질의 슬롯 2를 결정하므로 순서가 의미를 가진다.
test('parseKeywords trims blanks and keeps order', () => {
  assert.deepEqual(parseKeywords(' 대장내시경 , , 치질 '), ['대장내시경', '치질'])
})

test('parseKeywords removes duplicates', () => {
  assert.deepEqual(parseKeywords('치질, 치질, 대장내시경'), ['치질', '대장내시경'])
})

test('parseKeywords caps at the maximum the backend accepts', () => {
  assert.equal(parseKeywords('a, b, c, d, e, f').length, MAX_KEYWORDS)
})

// ── 프롬프트 인젝션 (PRD F1-4) ──────────────────────────────────────
// 병원명이 질의에 들어가면 언급은 보장되고 측정은 무의미해진다.
test('keyword carrying the hospital name is detected', () => {
  assert.equal(keywordContainsHospitalName('장편한외과의원', ['장편한외과의원 대장내시경']), true)
})

test('spacing and hyphen variants of the hospital name are also detected', () => {
  assert.equal(keywordContainsHospitalName('장편한 외과의원', ['장편한외과의원']), true)
  assert.equal(keywordContainsHospitalName('장편한-외과의원', ['장편한 외과 의원']), true)
})

test('ordinary treatment keywords pass', () => {
  assert.equal(keywordContainsHospitalName('장편한외과의원', ['대장내시경', '치질']), false)
})

// ── 형식 검사 ───────────────────────────────────────────────────────
test('usable phone shapes are accepted', () => {
  for (const value of ['02-123-4567', '+82 2 123 4567', '010-1234-5678', '(02)987-6543']) {
    assert.equal(isUsablePhone(value), true, value)
  }
})

test('unusable phone shapes are rejected', () => {
  // 대표번호는 1회 제한의 병원 측 키다 — 형식이 깨지면 잠금 자체가 성립하지 않는다.
  for (const value of ['', '1234', '없음', 'abc']) {
    assert.equal(isUsablePhone(value), false, value)
  }
})

test('email shape check accepts and rejects the obvious cases', () => {
  for (const value of ['a@b.com', 'doctor+tag@example.co.kr']) {
    assert.equal(isUsableEmail(value), true, value)
  }
  for (const value of ['', 'doctor', 'doctor@', '@example.com', 'a@@b.com', 'a@b']) {
    assert.equal(isUsableEmail(value), false, value)
  }
})

// ── 오타 교정 ───────────────────────────────────────────────────────
// 이중 영구 잠금 아래에서는 오타 하나가 그 병원의 유일한 기회를 태운다.
test('common domain typos get a correction suggestion', () => {
  assert.equal(suggestEmailCorrection('doctor@gmail.co'), 'doctor@gmail.com')
  assert.equal(suggestEmailCorrection('doctor@naver.co'), 'doctor@naver.com')
})

test('correct or unknown domains get no suggestion', () => {
  assert.equal(suggestEmailCorrection('doctor@gmail.com'), null)
  assert.equal(suggestEmailCorrection('doctor@hospital.kr'), null)
  assert.equal(suggestEmailCorrection('doctor'), null)
  assert.equal(suggestEmailCorrection('@gmail.co'), null)
})

// ── 폼 검증 ─────────────────────────────────────────────────────────
test('a complete form has no errors', () => {
  assert.deepEqual(validateDiagnosisForm(VALID), {})
})

test('every missing required field is reported at once', () => {
  // 한 번에 하나씩 알려주면 7단계 왕복이 된다.
  const errors = validateDiagnosisForm(EMPTY_FORM)
  assert.deepEqual(
    Object.keys(errors).sort(),
    [
      'clinicName',
      'clinicPhone',
      'clinicType',
      'contact',
      'contactName',
      'coreKeywords',
      'email',
      'privacy',
      'regionKeyword',
    ].sort(),
  )
})

test('a keyword carrying the hospital name is rejected by validation', () => {
  const errors = validateDiagnosisForm({ ...VALID, coreKeywords: '장편한외과의원 대장내시경' })
  assert.match(errors.coreKeywords ?? '', /병원명/)
})

test('privacy consent is required', () => {
  assert.ok(validateDiagnosisForm({ ...VALID, privacy: false }).privacy)
})

test('an unusable clinic phone is rejected', () => {
  assert.ok(validateDiagnosisForm({ ...VALID, clinicPhone: '없음' }).clinicPhone)
})

// ── 확인 모달 (PRD F1-8) ────────────────────────────────────────────
// 이 모달이 1회 제한 고지의 본체다 — 입력한 값을 입력한 그대로 보여줘야 한다.
test('confirmation rows show every value the applicant typed', () => {
  const rows = confirmationRows(VALID)
  assert.deepEqual(
    rows.map((row) => row.label),
    ['병원명', '지역', '대표번호', '진료과', '키워드', '담당자', '이메일'],
  )
  assert.equal(rows.find((row) => row.label === '이메일')?.value, 'doctor@example.com')
  assert.equal(rows.find((row) => row.label === '키워드')?.value, '대장내시경, 치질')
})

// ── 백엔드 계약 ─────────────────────────────────────────────────────
test('payload maps to the backend field names', () => {
  assert.deepEqual(toRequestPayload(VALID, '/ai-diagnosis'), {
    clinic_name: '장편한외과의원',
    clinic_type: '외과',
    region_keyword: '수서역',
    clinic_phone: '02-123-4567',
    core_keywords: ['대장내시경', '치질'],
    contact_name: '홍길동',
    contact: '010-1234-5678',
    email: 'doctor@example.com',
    privacy: true,
    source_path: '/ai-diagnosis',
  })
})

test('the hospital name never leaves as a measurement keyword', () => {
  // 질의는 백엔드가 만들지만, 폼이 병원명을 키워드로 흘려보내면 그 방어가 무의미해진다.
  const payload = toRequestPayload(VALID, '/ai-diagnosis')
  assert.equal(payload.core_keywords.includes(payload.clinic_name), false)
})
