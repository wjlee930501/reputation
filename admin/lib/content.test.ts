import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countCarriedOver,
  countUnpublishedCarriedOver,
  isCarriedOver,
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
  const items = [{ id: 'a' }, { id: 'b' }]

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
