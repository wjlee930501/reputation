import assert from 'node:assert/strict'
import test from 'node:test'

import { containsPatientSensitiveLeadText, leadSafetyError } from './lead-safety.ts'

test('lead safety rejects patient-sensitive free text before upstream submission', () => {
  assert.equal(containsPatientSensitiveLeadText('환자 홍길동 900101-1234567 수술 기록 상담'), true)
  assert.equal(containsPatientSensitiveLeadText('환자 홍길동 9001011234567 수술 기록 상담'), true)
  assert.equal(containsPatientSensitiveLeadText('환자 홍길동 900101 1234567 진료 기록 상담'), true)
  assert.equal(containsPatientSensitiveLeadText('환자 홍길동 수술 기록 상담'), true)
  assert.equal(containsPatientSensitiveLeadText('어제 검사 결과와 처방 내역 확인 부탁드립니다.'), true)
  assert.equal(containsPatientSensitiveLeadText('환자 유입 상담을 받고 싶습니다.'), false)
  assert.equal(containsPatientSensitiveLeadText('환자 리뷰 마케팅 개선 방안을 알고 싶습니다.'), false)
  assert.equal(containsPatientSensitiveLeadText('병원 마케팅 진단을 받고 싶습니다.'), false)
  assert.match(leadSafetyError(), /환자 개인정보/)
})
