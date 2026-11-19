import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ADMIN_COPY } from './admin-copy.ts'
import { doctorSurveyStatusText, type DoctorSurveyStatus } from './doctor-survey.ts'

const base: DoctorSurveyStatus = { hospital_id: 'h', status: 'NOT_OPENED', link_url: 'https://x/survey/t' }

test('원장 설문 상태 한 줄 — 안 열어 봄·작성 중·제출됨', () => {
  assert.equal(doctorSurveyStatusText(base), ADMIN_COPY.doctorSurvey.statusNotOpened)
  assert.equal(
    doctorSurveyStatusText({ ...base, status: 'IN_PROGRESS', progress: { answered: 4, total: 23 } }),
    '작성 중 · 4/23문항 답함',
  )
  const submitted = doctorSurveyStatusText({ ...base, status: 'SUBMITTED', last_submitted_at: '2026-11-19T05:00:00Z' })
  assert.match(submitted, /^제출됨 · /)
  assert.doesNotMatch(submitted, /\{date\}/)
  assert.equal(doctorSurveyStatusText({ ...base, status: 'NOT_ISSUED', link_url: null }), '')
})

test('원장이 올린 파일 종류 이름', async () => {
  const { doctorSurveyFileKindLabel } = await import('./doctor-survey.ts')
  assert.equal(doctorSurveyFileKindLabel('PHOTO'), '사진')
  assert.equal(doctorSurveyFileKindLabel('LOGO'), '로고')
  assert.equal(doctorSurveyFileKindLabel('CERTIFICATE'), '지정서·인증서')
})

test('고른 반영 가능 항목의 patch만 합친다', async () => {
  const { mergeFactPatches } = await import('./doctor-survey.ts')
  const facts = [
    { field: 'phone', label: '', value: '', current: '', applicable: true, patch: { phone: '02-1' }, reason: null },
    { field: 'specialties', label: '', value: '', current: '', applicable: true, patch: { specialties: ['정형외과'] }, reason: null },
    { field: 'competitors', label: '', value: '', current: '', applicable: false, patch: null, reason: '측정 기준' },
    { field: 'address', label: '', value: '', current: '기존', applicable: false, patch: { address: 'x' }, reason: null },
  ]
  assert.deepEqual(mergeFactPatches(facts, new Set(['phone', 'competitors', 'address'])), { phone: '02-1' })
})
