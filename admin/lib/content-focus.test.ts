import assert from 'node:assert/strict'
import test from 'node:test'

import {
  addMonths,
  draftChanged,
  draftFromState,
  focusSummary,
  matchLabel,
  monthProgress,
  monthShortfall,
  periodLabel,
  periodProblem,
  topicInputProblem,
  type ContentFocusState,
} from './content-focus.ts'

const STATE: ContentFocusState = {
  topics: ['내시경', '여의사 진료'],
  share: 50,
  from_month: '2026-11',
  until_month: '2026-12',
  legacy_topics: [],
  share_choices: [30, 50, 70, 100],
  max_topics: 5,
  topic_matches: [],
  months: [],
}

test('summary line says on/off, topics, period and share', () => {
  assert.equal(focusSummary(null), '꺼짐')
  assert.equal(focusSummary({ ...STATE, topics: [] }), '꺼짐')
  assert.equal(focusSummary(STATE), '켜짐 · 내시경 외 1개 · 2026년 11월 ~ 2026년 12월 · 50%')
})

test('period labels cover open ends', () => {
  assert.equal(periodLabel('2026-11', '2026-11'), '2026년 11월')
  assert.equal(periodLabel('2026-11', null), '2026년 11월부터 계속')
  assert.equal(periodLabel(null, '2026-12'), '지금부터 2026년 12월까지')
  assert.equal(periodLabel(null, null), '기간 없이 계속')
  assert.equal(periodProblem('2026-12', '2026-11'), '끝 달이 시작 달보다 앞설 수 없습니다.')
  assert.equal(periodProblem('2026-11', ''), null)
})

test('topic input problems', () => {
  assert.equal(topicInputProblem('  ', [], 5), null)
  assert.equal(topicInputProblem('내시경', ['내시경'], 5), '이미 넣은 주제입니다.')
  assert.equal(topicInputProblem('내', [], 5), '주제는 2~20자로 적어 주세요.')
  assert.equal(topicInputProblem('초음파', ['a1', 'a2', 'a3', 'a4', 'a5'], 5), '주력 주제는 5개까지 정할 수 있습니다.')
  assert.equal(topicInputProblem('  내시경  검사 ', [], 5), null)
})

test('month progress and shortfall', () => {
  const month = { month: '2026-11', active: true, total: 12, focused: 1, quota: 6, open_slots: 3 }
  assert.equal(monthProgress(month), '11월 글 12편 중 주력 주제 목표 6편 · 지금 1편 · 아직 주제가 정해지지 않은 글 3편')
  assert.equal(monthShortfall(month), '11월은 주제가 이미 정해진 글이 많아 목표보다 2편 적게 쓰일 수 있습니다.')
  assert.equal(monthShortfall({ ...month, open_slots: 5 }), null)
  assert.equal(monthProgress({ ...month, active: false }), null)
})

test('match label tones', () => {
  assert.equal(matchLabel(undefined).tone, 'pending')
  assert.deepEqual(matchLabel({ topic: '여의사', matching_questions: 0, examples: [] }), {
    text: '맞는 환자 질문 없음 — 이 주제로는 글을 고를 수 없습니다',
    tone: 'warn',
  })
  assert.equal(matchLabel({ topic: '내시경', matching_questions: 11, examples: [] }).text, '맞는 환자 질문 11개')
})

test('draft change detection and month arithmetic', () => {
  const draft = draftFromState(STATE)
  assert.equal(draftChanged(STATE, draft), false)
  assert.equal(draftChanged(STATE, { ...draft, share: 70 }), true)
  assert.equal(draftChanged(STATE, { ...draft, topics: ['내시경'] }), true)
  assert.deepEqual(draftFromState(null), { topics: [], share: 50, fromMonth: '', untilMonth: '' })
  assert.equal(addMonths('2026-11', 1), '2026-12')
  assert.equal(addMonths('2026-12', 1), '2027-01')
  assert.equal(addMonths('2026-01', -1), '2025-12')
})
