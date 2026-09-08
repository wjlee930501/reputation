import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ROW_FILTERS,
  ROW_FILTER_LABELS,
  canConfirmSample,
  describeRowState,
  matchesRowFilter,
  summarizeRows,
} from './content-rows.ts'
import type { ContentRowItem } from './content-rows.ts'
import type { ContentRowState } from '../types/index.ts'

function rowState(partial: Partial<ContentRowState> & { kind: ContentRowState['kind'] }): ContentRowState {
  return { label: '', reason: null, link: null, ...partial }
}

function item(partial: Partial<ContentRowItem> & { kind: ContentRowState['kind'] }): ContentRowItem {
  const { kind, ...rest } = partial
  return { row_state: rowState({ kind, label: ROW_FILTER_LABELS[kind] }), ...rest }
}

test('공개 중인 글은 사유도 링크도 없이 정상으로 표시된다', () => {
  const described = describeRowState(rowState({ kind: 'public', label: '공개 중' }))
  assert.deepEqual(described, { label: '공개 중', detail: null, tone: 'good', href: null })
})

test('공개 보류는 사유를 그대로 보여 준다', () => {
  const described = describeRowState(
    rowState({ kind: 'withheld', label: '공개 보류', reason: '대표 이미지 재인증 대기' }),
  )
  assert.deepEqual(described, {
    label: '공개 보류',
    detail: '대표 이미지 재인증 대기',
    tone: 'warn',
    href: null,
  })
})

test('차단은 운영 센터 링크와 다음 조치를 함께 준다', () => {
  const described = describeRowState(
    rowState({
      kind: 'blocked',
      label: '차단',
      reason: null,
      link: { kind: 'incident', href: '/operations/hospitals/h1/incidents/i1', next_action: '이미지 재생성' },
    }),
  )
  assert.equal(described.tone, 'warn')
  assert.equal(described.detail, '이미지 재생성')
  assert.equal(described.href, '/operations/hospitals/h1/incidents/i1')
})

test('예정·초안 생성 중은 사람이 할 일이 아니다', () => {
  assert.equal(describeRowState(rowState({ kind: 'scheduled', label: '예정' })).tone, 'neutral')
  const generating = describeRowState(
    rowState({ kind: 'generating', label: '초안 생성 중', reason: '발행 전날 23:00 자동 생성' }),
  )
  assert.equal(generating.tone, 'neutral')
  assert.equal(generating.detail, '발행 전날 23:00 자동 생성')
})

test('종료된 항목은 회색 톤으로 남는다', () => {
  const described = describeRowState(rowState({ kind: 'closed', label: '종료', reason: '종료됨' }))
  assert.equal(described.tone, 'paused')
  assert.equal(described.detail, '종료됨')
})

test('표본 확인은 공개 페이지에 실제로 있는 미확인 표본에만 열린다', () => {
  const sample: ContentRowItem = {
    ...item({ kind: 'public' }),
    status: 'PUBLISHED',
    post_publish_review_required: true,
    post_publish_reviewed_at: null,
    compliance: { public_visibility: { visible: true } },
  }
  assert.equal(canConfirmSample(sample), true)
  assert.equal(canConfirmSample({ ...sample, post_publish_review_required: false }), false)
  assert.equal(canConfirmSample({ ...sample, post_publish_reviewed_at: '2026-09-09T00:00:00+09:00' }), false)
  assert.equal(
    canConfirmSample({ ...sample, compliance: { public_visibility: { visible: false } } }),
    false,
  )
  assert.equal(canConfirmSample({ ...sample, status: 'DRAFT' }), false)
})

test('판정이 없는 응답에는 확인 버튼을 열지 않는다', () => {
  const missingVisibility: ContentRowItem = {
    ...item({ kind: 'public' }),
    status: 'PUBLISHED',
    post_publish_review_required: true,
  }
  assert.equal(canConfirmSample(missingVisibility), false)
})

test('요약은 행 상태별 건수와 이월 건수를 함께 센다', () => {
  const totals = summarizeRows([
    item({ kind: 'public' }),
    item({ kind: 'public', carried_over_from: '2026-08-20' }),
    item({ kind: 'withheld' }),
    item({ kind: 'scheduled' }),
    item({ kind: 'generating' }),
    item({ kind: 'blocked' }),
    item({ kind: 'closed' }),
  ])
  assert.deepEqual(totals, {
    public: 2,
    withheld: 1,
    scheduled: 1,
    generating: 1,
    blocked: 1,
    closed: 1,
    carried: 1,
  })
})

test('필터는 전체·이월과 여섯 개 행 상태를 같은 이름으로 다룬다', () => {
  assert.deepEqual([...ROW_FILTERS], [
    'all',
    'carried',
    'public',
    'withheld',
    'scheduled',
    'generating',
    'blocked',
    'closed',
  ])
  assert.equal(ROW_FILTER_LABELS.public, '공개 중')
  assert.equal(ROW_FILTER_LABELS.withheld, '공개 보류')
  assert.equal(ROW_FILTER_LABELS.scheduled, '예정')
  assert.equal(ROW_FILTER_LABELS.generating, '초안 생성 중')
  assert.equal(ROW_FILTER_LABELS.blocked, '차단')
  assert.equal(ROW_FILTER_LABELS.closed, '종료')

  const carried = item({ kind: 'blocked', carried_over_from: '2026-08-20' })
  assert.equal(matchesRowFilter(carried, 'all'), true)
  assert.equal(matchesRowFilter(carried, 'carried'), true)
  assert.equal(matchesRowFilter(carried, 'blocked'), true)
  assert.equal(matchesRowFilter(carried, 'public'), false)
})
