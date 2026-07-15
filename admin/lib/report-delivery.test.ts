import assert from 'node:assert/strict'
import test from 'node:test'

import { readReportDeliveryState } from './report-delivery.ts'

test('backend delivery_ready is the only positive delivery authority', () => {
  assert.deepEqual(readReportDeliveryState({ delivery_ready: true, delivery_blockers: [] }), {
    ready: true,
    blockers: [],
  })
  assert.equal(readReportDeliveryState({}).ready, false)
  assert.equal(readReportDeliveryState({ delivery_ready: false }).ready, false)
  assert.equal(
    readReportDeliveryState({ delivery_ready: true, delivery_blockers: ['계약 불일치'] }).ready,
    false,
  )
})

test('backend blockers are preserved and blank entries are removed', () => {
  assert.deepEqual(
    readReportDeliveryState({
      delivery_ready: false,
      delivery_blockers: ['PDF가 준비되지 않았습니다.', '  '],
    }).blockers,
    ['PDF가 준비되지 않았습니다.'],
  )
})
