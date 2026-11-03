import assert from 'node:assert/strict'
import test from 'node:test'

import { buildTreatmentAnswer } from './treatment-answer.ts'

test('buildTreatmentAnswer answers where, who and how to book from approved facts only', () => {
  const answer = buildTreatmentAnswer({
    hospitalName: '강심장내과의원',
    treatmentName: '심장초음파',
    address: '서울특별시 강남구 테헤란로 1 2층',
    specialties: ['내과', '내과', '심장내과'],
    physicians: [
      { name: '김의사', suffix: '원장', certifications: ['내과 전문의', '심장학회 정회원', '추가'] },
      { name: '이의사', suffix: null, certifications: [] },
    ],
    phone: '02-000-0000',
    hasWeeklyHours: true,
  })
  assert.ok(answer)
  assert.equal(answer.heading, '심장초음파 진료 기본 정보')
  assert.deepEqual(answer.sentences, [
    '심장초음파 진료는 강심장내과의원에서 받을 수 있습니다.',
    '병원 위치는 서울특별시 강남구 테헤란로 1 2층입니다.',
    '진료를 맡는 의료진은 김의사 원장(내과 전문의, 심장학회 정회원), 이의사입니다.',
    '예약과 문의 전화는 02-000-0000입니다.',
  ])
  assert.deepEqual(
    answer.facts.map((fact) => fact.label),
    ['진료 항목', '진료과', '위치', '담당 의료진', '전화', '진료시간'],
  )
  assert.equal(answer.facts[1].value, '내과 · 심장내과')
  // 비교·우위·유인 표현을 템플릿이 스스로 만들지 않는다(의료법 제56조).
  const text = [...answer.sentences, answer.heading].join(' ')
  for (const term of ['추천', '최고', '잘하는', '유명', '최저', '할인', '완치', '보장', '1위']) {
    assert.ok(!text.includes(term), term)
  }
})

test('buildTreatmentAnswer omits missing facts and refuses empty names', () => {
  const answer = buildTreatmentAnswer({
    hospitalName: '정형외과의원',
    treatmentName: '도수치료',
    address: '',
    specialties: [],
    physicians: [],
    phone: null,
    hasWeeklyHours: false,
  })
  assert.deepEqual(answer?.sentences, ['도수치료 진료는 정형외과의원에서 받을 수 있습니다.'])
  assert.deepEqual(answer?.facts, [{ label: '진료 항목', value: '도수치료' }])
  assert.equal(
    buildTreatmentAnswer({
      hospitalName: ' ',
      treatmentName: '도수치료',
      address: '',
      specialties: [],
      physicians: [],
      phone: null,
      hasWeeklyHours: false,
    }),
    null,
  )
})
