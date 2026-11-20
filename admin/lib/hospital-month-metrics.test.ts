import assert from 'node:assert/strict'
import test from 'node:test'

import { mentionRateLabel, metricsByHospital, publishProgress, sortHospitals } from './hospital-month-metrics.ts'

const m = (id: string, pub: number, plan: number, rate: number | null) => ({
  hospital_id: id, year: 2026, month: 11, published_count: pub, public_count: pub, planned_total: plan,
  mention_rate: rate, mention_rate_measured_at: null,
})

test('publish progress and mention rate read like the status tab; no measurement is not 0%', () => {
  assert.deepEqual(publishProgress(m('a', 6, 12, 10)), { ratio: 0.5, label: '6 / 12' })
  assert.deepEqual(publishProgress(m('a', 3, 0, 10)), { ratio: 0, label: '3 / 0' })
  assert.equal(publishProgress(undefined), null)
  assert.equal(mentionRateLabel(m('a', 0, 12, null)), '측정 전')
  assert.equal(mentionRateLabel(m('a', 0, 12, 0)), '0.0%')
  assert.equal(mentionRateLabel(undefined), '—')
})

test('sorting puts rows without a value last and keeps ties in order', () => {
  const rows = [{ id: 'a', name: '나병원' }, { id: 'b', name: '가병원' }, { id: 'c', name: '다병원' }]
  const metrics = metricsByHospital([m('a', 6, 12, null), m('b', 12, 12, 20.5), { bad: true }])
  assert.deepEqual(sortHospitals(rows, 'name', metrics).map((r) => r.id), ['b', 'a', 'c'])
  assert.deepEqual(sortHospitals(rows, 'publish', metrics).map((r) => r.id), ['b', 'a', 'c'])
  assert.deepEqual(sortHospitals(rows, 'mention', metrics).map((r) => r.id), ['b', 'a', 'c'])
  assert.deepEqual(sortHospitals(rows, 'created', metrics).map((r) => r.id), ['a', 'b', 'c'])
})
