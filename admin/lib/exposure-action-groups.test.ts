import assert from 'node:assert/strict'
import test from 'node:test'

import { groupExposureActions } from './exposure-action-groups.ts'
import type { ExposureAction } from '../types/index.ts'

function action(id: string, gapType: string, questionId: string): ExposureAction {
  return {
    id,
    hospital_id: 'hospital-1',
    query_target_id: questionId,
    gap_id: `gap-${id}`,
    gap_type: gapType,
    severity: 'HIGH',
    evidence: {},
    action_type: 'CONTENT',
    title: '근거 콘텐츠 보강',
    description: '설명',
    owner: 'owner@example.test',
    due_month: '2026-08',
    status: 'OPEN',
    linked_content_id: null,
    linked_content: null,
    linked_report_id: null,
    completed_at: null,
    created_at: null,
    updated_at: null,
    query_target: {
      id: questionId,
      name: `질문 ${questionId}`,
      target_intent: '추천형',
      priority: 'NORMAL',
      status: 'ACTIVE',
      target_month: null,
    },
  }
}

test('same diagnosis type becomes one card with a question checklist count', () => {
  const grouped = groupExposureActions([
    action('a-1', 'MISSING_MENTION', 'q-1'),
    action('a-2', 'MISSING_MENTION', 'q-2'),
    action('a-3', 'SOURCE_GAP', 'q-3'),
  ])

  assert.equal(grouped.length, 2)
  assert.equal(grouped[0].questionCount, 2)
  assert.deepEqual(grouped[0].actions.map((item) => item.id), ['a-1', 'a-2'])
  assert.equal(grouped[0].commonOwner, 'owner@example.test')
})
