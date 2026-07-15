import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildPublicContentUrl,
  countCarriedOver,
  countUnpublishedCarriedOver,
  getContentOperationsState,
  isCarriedOver,
  matchesContentOperationsFilter,
  sortCarriedOverFirst,
} from './content.ts'

test('isCarriedOver only flags items with carried_over_from set', () => {
  assert.equal(isCarriedOver({ carried_over_from: '2026-05-28' }), true)
  assert.equal(isCarriedOver({ carried_over_from: null }), false)
  assert.equal(isCarriedOver({}), false)
})

test('sortCarriedOverFirst moves carried items to the top, preserving order in both groups', () => {
  const items = [
    { id: 'a', carried_over_from: null },
    { id: 'b', carried_over_from: '2026-05-26' },
    { id: 'c', carried_over_from: null },
    { id: 'd', carried_over_from: '2026-05-28' },
  ]

  const sorted = sortCarriedOverFirst(items)

  assert.deepEqual(
    sorted.map((item) => item.id),
    ['b', 'd', 'a', 'c'],
  )
})

test('sortCarriedOverFirst keeps order untouched when nothing is carried over', () => {
  const items = [
    { id: 'a', carried_over_from: null },
    { id: 'b', carried_over_from: null },
  ]

  assert.deepEqual(
    sortCarriedOverFirst(items).map((item) => item.id),
    ['a', 'b'],
  )
})

test('countCarriedOver counts all carried items regardless of status', () => {
  const items = [
    { carried_over_from: '2026-05-26', status: 'PUBLISHED' },
    { carried_over_from: '2026-05-28', status: 'DRAFT' },
    { carried_over_from: null, status: 'DRAFT' },
  ]

  assert.equal(countCarriedOver(items), 2)
})

test('countUnpublishedCarriedOver excludes published carried items', () => {
  const items = [
    { carried_over_from: '2026-05-26', status: 'PUBLISHED' },
    { carried_over_from: '2026-05-28', status: 'DRAFT' },
    { carried_over_from: '2026-05-30', status: 'REJECTED' },
    { carried_over_from: null, status: 'DRAFT' },
  ]

  assert.equal(countUnpublishedCarriedOver(items), 2)
})

test('content operations state distinguishes Slack retry, post-review, and reviewed states', () => {
  assert.equal(getContentOperationsState({ status: 'PUBLISHED' }), 'notificationPending')
  assert.equal(
    getContentOperationsState({ status: 'PUBLISHED', post_publish_notified_at: '2026-07-16T08:00:00Z' }),
    'postReviewPending',
  )
  assert.equal(
    getContentOperationsState({
      status: 'PUBLISHED',
      post_publish_notified_at: '2026-07-16T08:00:00Z',
      post_publish_reviewed_at: '2026-07-16T09:00:00Z',
    }),
    'published',
  )
  assert.equal(getContentOperationsState({ status: 'DRAFT', title: null }), 'notGenerated')
  assert.equal(
    getContentOperationsState({ status: 'DRAFT', title: 'blocked', compliance: { publishable: false } }),
    'needsReview',
  )
  assert.equal(
    getContentOperationsState({ status: 'DRAFT', title: 'ready', compliance: { publishable: true } }),
    'publishable',
  )
})

test('content operations filters support actionable summary-card filtering', () => {
  const item = {
    status: 'PUBLISHED',
    carried_over_from: '2026-06-30',
    post_publish_notified_at: '2026-07-16T08:00:00Z',
  }
  assert.equal(matchesContentOperationsFilter(item, 'all'), true)
  assert.equal(matchesContentOperationsFilter(item, 'carried'), true)
  assert.equal(matchesContentOperationsFilter(item, 'postReviewPending'), true)
  assert.equal(matchesContentOperationsFilter(item, 'notificationPending'), false)
})

test('buildPublicContentUrl normalizes schemes, slashes, and content ids', () => {
  assert.equal(buildPublicContentUrl('https://jangclinic.kr/', 'content 1'), 'https://jangclinic.kr/contents/content%201')
  assert.equal(buildPublicContentUrl('jangclinic.kr', 'abc'), 'https://jangclinic.kr/contents/abc')
  assert.equal(buildPublicContentUrl(null, 'abc'), null)
})
