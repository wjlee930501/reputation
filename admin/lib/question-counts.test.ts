import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  QUESTION_COUNT_LABELS,
  describeQuestionPhraseCounts,
  summarizeQuestionCounts,
} from './question-counts.ts'

function target(status: string) {
  return { status, summary: { variant_count: 2, active_variant_count: 2 } }
}

test('question topics and question phrases are counted separately', () => {
  const summary = summarizeQuestionCounts(
    [target('ACTIVE'), target('ACTIVE'), target('PAUSED'), target('ARCHIVED')],
    [{ total_count: 4 }, { total_count: 0 }, { total_count: 2 }],
  )

  assert.equal(summary.topicsOperating, 2)
  assert.equal(summary.topicsPaused, 1)
  assert.equal(summary.topicsArchived, 1)
  assert.equal(summary.topicsTracked, 3)
  assert.equal(summary.phrasesLinked, 3)
})

test('a phrase with no successful measurement is waiting, never measured', () => {
  const summary = summarizeQuestionCounts([target('ACTIVE')], [
    { total_count: 0 },
    { total_count: 0 },
    { total_count: 7 },
  ])

  assert.equal(summary.phrasesMeasured, 1)
  assert.equal(summary.phrasesWaiting, 2)
})

test('measured plus waiting always reconciles to the linked phrase count', () => {
  const rows = [{ total_count: 0 }, { total_count: 3 }, {}, { total_count: 1 }]
  const summary = summarizeQuestionCounts([target('ACTIVE')], rows)

  assert.equal(summary.phrasesMeasured + summary.phrasesWaiting, summary.phrasesLinked)
  assert.equal(summary.phrasesLinked, rows.length)
})

test('an empty matrix says so instead of reporting zero measured questions', () => {
  const summary = summarizeQuestionCounts([target('ACTIVE')], [])

  assert.equal(describeQuestionPhraseCounts(summary), '측정표에 연결된 질문 문구가 없습니다')
})

test('the phrase hint names waiting phrases before the first measurement', () => {
  const summary = summarizeQuestionCounts([target('ACTIVE')], [{ total_count: 0 }, { total_count: 0 }])

  assert.equal(
    describeQuestionPhraseCounts(summary),
    `${QUESTION_COUNT_LABELS.phrasesWaiting} 2개 · 아직 측정 전`,
  )
})

test('the phrase hint drops the waiting clause once every phrase is measured', () => {
  const summary = summarizeQuestionCounts([target('ACTIVE')], [{ total_count: 1 }, { total_count: 2 }])

  assert.equal(
    describeQuestionPhraseCounts(summary),
    `${QUESTION_COUNT_LABELS.phrasesMeasured} 2개`,
  )
})

test('both screens take the question vocabulary from the shared module', () => {
  const dashboard = readFileSync(
    new URL('../app/hospitals/[id]/dashboard/page.tsx', import.meta.url),
    'utf8',
  )
  const queryTargets = readFileSync(
    new URL('../app/hospitals/[id]/query-targets/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(dashboard, /summarizeQuestionCounts/)
  assert.match(dashboard, /QUESTION_COUNT_LABELS/)
  assert.match(queryTargets, /QUESTION_COUNT_LABELS/)
  // "측정한 환자 질문"은 측정되지 않은 문구까지 세던 이름이다 — 되살아나면 실패한다.
  assert.doesNotMatch(dashboard, /측정한 환자 질문/)
  assert.doesNotMatch(dashboard, /측정 질문표/)
  assert.doesNotMatch(dashboard, /측정용 질문/)
})
