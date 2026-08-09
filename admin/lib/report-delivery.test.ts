import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getCustomerReportDownload,
  isEffectivelyDelivered,
  readReportDeliveryState,
} from './report-delivery.ts'

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

test('a rescission overrides sent_at until a re-delivery event exists', () => {
  assert.equal(
    isEffectivelyDelivered({ sent_at: '2026-08-10T10:00:00Z', effective_delivery: { event_type: 'RESCINDED' } }),
    false,
  )
  assert.equal(
    isEffectivelyDelivered({ sent_at: '2026-08-10T10:00:00Z', effective_delivery: { event_type: 'REDELIVERED' } }),
    true,
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

test('customer download is the validated doctor artifact and never the AE fallback', () => {
  assert.equal(
    getCustomerReportDownload({
      hospital_id: 'hospital-1',
      id: 'report-1',
      doctor_artifact_state: 'VALID',
      delivery_ready: true,
    }),
    '/api/admin/hospitals/hospital-1/reports/report-1/download?audience=doctor',
  )
  assert.equal(
    getCustomerReportDownload({
      hospital_id: 'hospital-1',
      id: 'report-1',
      doctor_artifact_state: 'MISSING',
      delivery_ready: true,
      download_url: '/api/admin/hospitals/hospital-1/reports/report-1/download',
    }),
    null,
  )
  assert.equal(
    getCustomerReportDownload({
      hospital_id: 'hospital-1',
      id: 'report-1',
      doctor_artifact_state: 'VALID',
      delivery_ready: false,
    }),
    null,
  )
})
