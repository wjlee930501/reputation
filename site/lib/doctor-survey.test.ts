import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  aiQuestionOptions,
  buildFlow,
  compactAnswers,
  iterFields,
  progress,
  questionAnswered,
  questionTitle,
  resolveStep,
  treatmentNames,
  type SurveySchema,
} from './doctor-survey.ts'
import { surveyUpstreamPath } from './doctor-survey-proxy.ts'

const schema = {
  version: 't',
  focus_key: 'focus_answers',
  focus_max: 5,
  ai_question_templates: ['{town}에서 {focus} 잘하는 {dept}'],
  ai_question_fallbacks: { town: '우리 동네', dept: '진료과', focus: '이 진료' },
  parts: [
    {
      name: '기본',
      sum: '',
      qs: [
        { id: 'clinic', n: '', t: '병원명', b: [{ t: 'text', key: 'clinic_name' }] },
        {
          id: 'prices',
          n: '8',
          t: '가격',
          b: [{ t: 'single', key: 'prices_public', opts: ['있음'], reveal: { 있음: [{ t: 'list', key: 'prices', fields: [] }] } }],
        },
      ],
    },
    { name: '진료별', sum: '', focus: true, qs: [] },
    {
      name: '이야기',
      sum: '',
      qs: [
        { id: 'opening', n: '37', t: '개원', new_clinic_only: true, b: [{ t: 'area', key: 'opening_story' }] },
        {
          id: 'faq',
          n: '35',
          t: '질문',
          b: [{ t: 'toggles', items: [{ t: 'toggle', key: 'faq_1', children: [{ t: 'text', key: 'faq_1_answer' }] }] }],
        },
      ],
    },
  ],
  focus_questions: [
    { id: 'recommend', n: '30', t: '{nm} 권하는 경우', b: [{ t: 'text', key: 'recommend_when' }] },
    { id: 'care', n: '33', t: '당부', b: [{ t: 'text', key: 'aftercare' }] },
  ],
} as unknown as SurveySchema

test('흐름은 시작 → 문항 → 확인 → 완료이고 개원 문항은 개원 병원만', () => {
  const keys = buildFlow(schema, {}, false).map((item) => item.key)
  assert.deepEqual(keys, ['intro', 'q|clinic', 'q|prices', 'q|faq', 'review', 'done'])
  assert.ok(buildFlow(schema, {}, true).some((item) => item.key === 'q|opening'))
})

test('주력 진료마다 3부 문항이 반복된다', () => {
  const flow = buildFlow(schema, { focus_treatments: ['충격파', '도수'] }, false)
  const focusKeys = flow.filter((item) => item.focus).map((item) => item.key)
  assert.deepEqual(focusKeys, [
    'focus|충격파|recommend',
    'focus|충격파|care',
    'focus|도수|recommend',
    'focus|도수|care',
  ])
  assert.equal(questionTitle(flow.find((item) => item.focus === '도수')!.question!, '도수'), '도수 권하는 경우')
})

test('펼침·스위치 아래 칸도 답한 것으로 센다(서버와 같은 규칙)', () => {
  assert.deepEqual(
    iterFields(schema.parts[2].qs[1].b).map((field) => field.key),
    ['faq_1', 'faq_1_answer'],
  )
  const flow = buildFlow(schema, {}, false)
  const faq = flow.find((item) => item.key === 'q|faq')!
  assert.equal(questionAnswered(schema, { faq_1_answer: '  ' }, faq), false)
  assert.equal(questionAnswered(schema, { faq_1_answer: '대부분 괜찮습니다' }, faq), true)
  const focusFlow = buildFlow(schema, { focus_treatments: ['충격파'] }, false)
  const recommend = focusFlow.find((item) => item.key === 'focus|충격파|recommend')!
  assert.equal(
    questionAnswered(schema, { focus_answers: { 충격파: { recommend_when: '6주 넘게' } } }, recommend),
    true,
  )
  assert.deepEqual(progress(schema, { clinic_name: 'A의원' }, false), { answered: 1, total: 3 })
})

test('AI 질문 보기는 동네·진료과·주력 진료로 채우고, 비면 대체어를 쓴다', () => {
  assert.deepEqual(aiQuestionOptions(schema, {}), ['우리 동네에서 이 진료 잘하는 진료과'])
  assert.deepEqual(
    aiQuestionOptions(schema, { towns: ['도곡동'], departments: ['정형외과'], focus_treatments: ['충격파'] }),
    ['도곡동에서 충격파 잘하는 정형외과'],
  )
})

test('진료비 칸의 진료 보기는 5번 진료 이름이다', () => {
  assert.deepEqual(treatmentNames({ treatments: [{ name: '도수치료' }, { name: ' ' }, { name: '도수치료' }, 'x'] }), [
    '도수치료',
  ])
})

test('없는 화면 key면 처음으로, 빈 답은 보내지 않는다', () => {
  const flow = buildFlow(schema, {}, false)
  assert.equal(resolveStep(flow, 'focus|사라진진료|care'), 0)
  assert.equal(resolveStep(flow, 'review'), flow.length - 2)
  assert.deepEqual(compactAnswers({ a: '', b: [], c: 'x', d: false, e: {} }), { c: 'x', d: false })
})

test('프록시는 정해진 동작과 토큰 모양만 통과시킨다', () => {
  const token = 'A'.repeat(43)
  assert.equal(surveyUpstreamPath('GET', token, undefined), `/doctor-survey/${token}`)
  assert.equal(surveyUpstreamPath('PUT', token, ['answers']), `/doctor-survey/${token}/answers`)
  assert.equal(surveyUpstreamPath('POST', token, ['submit']), `/doctor-survey/${token}/submit`)
  assert.equal(surveyUpstreamPath('POST', token, ['answers']), null)
  assert.equal(surveyUpstreamPath('DELETE', token, undefined), null)
  assert.equal(surveyUpstreamPath('GET', '../admin', undefined), null)
  assert.equal(surveyUpstreamPath('GET', token, ['..', 'x']), null)
})
