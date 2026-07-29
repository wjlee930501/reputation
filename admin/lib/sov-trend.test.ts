import assert from 'node:assert/strict'
import test from 'node:test'

import { summarizeSovTrend } from './sov-trend.ts'

test('summarizeSovTrend reports the weekly delta when both weeks were measured', () => {
  const summary = summarizeSovTrend([{ sov_pct: 40 }, { sov_pct: 55 }])

  assert.equal(summary.current, 55)
  assert.equal(summary.change, 15)
  assert.equal(summary.state, 'MEASURED')
  assert.equal(summary.hint, '전주 대비 +15.0%p')
})

test('summarizeSovTrend separates "never measured" from "not measured this week"', () => {
  const never = summarizeSovTrend([{ sov_pct: null }, { sov_pct: null }])
  assert.equal(never.state, 'NEVER_MEASURED')
  assert.equal(never.hint, '아직 측정 전')

  const thisWeek = summarizeSovTrend([{ sov_pct: 30 }, { sov_pct: null }])
  assert.equal(thisWeek.state, 'NOT_MEASURED_THIS_WEEK')
  assert.equal(thisWeek.hint, '이번 주 측정 없음')
  assert.equal(thisWeek.current, null)
})

test('summarizeSovTrend never turns a missing week into a 0% delta', () => {
  const summary = summarizeSovTrend([{ sov_pct: null }, { sov_pct: 20 }])

  assert.equal(summary.current, 20)
  assert.equal(summary.change, null)
  assert.equal(summary.hint, '전주 측정 없음 — 추세 비교 불가')
})

test('summarizeSovTrend treats an empty trend as never measured', () => {
  const summary = summarizeSovTrend([])

  assert.equal(summary.current, null)
  assert.equal(summary.change, null)
  assert.equal(summary.state, 'NEVER_MEASURED')
})
