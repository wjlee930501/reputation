import assert from 'node:assert/strict'
import test from 'node:test'

import { summarizeSovTrend, trimTrendToMeasuredWeeks } from './sov-trend.ts'

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

test('weeks before the first measurement never reach the chart', () => {
  const trimmed = trimTrendToMeasuredWeeks([
    { week_start: '2026-06-01', sov_pct: null, total_count: 0 },
    { week_start: '2026-06-08', sov_pct: null, total_count: 0 },
    { week_start: '2026-06-15', sov_pct: 18, total_count: 150 },
    { week_start: '2026-06-22', sov_pct: 21, total_count: 150 },
  ])

  assert.deepEqual(
    trimmed.map((week) => week.week_start),
    ['2026-06-15', '2026-06-22'],
  )
})

test('a gap after the first measurement stays visible — a skipped week is real', () => {
  const trimmed = trimTrendToMeasuredWeeks([
    { week_start: '2026-06-01', sov_pct: null, total_count: 0 },
    { week_start: '2026-06-08', sov_pct: 18, total_count: 150 },
    { week_start: '2026-06-15', sov_pct: null, total_count: 0 },
    { week_start: '2026-06-22', sov_pct: 21, total_count: 150 },
  ])

  assert.equal(trimmed.length, 3)
  assert.equal(trimmed[1].sov_pct, null)
})

test('a week that was measured but produced no mention is a measurement start, not a blank', () => {
  const trimmed = trimTrendToMeasuredWeeks([
    { week_start: '2026-06-01', sov_pct: null, total_count: 0 },
    // 측정은 돌았지만 판정이 확정된 건이 없어 sov_pct가 null인 주 — total_count로 구분한다.
    { week_start: '2026-06-08', sov_pct: null, total_count: 12 },
    { week_start: '2026-06-15', sov_pct: 18, total_count: 150 },
  ])

  assert.equal(trimmed.length, 2)
  assert.equal(trimmed[0].week_start, '2026-06-08')
})

test('a week with only failures starts the measured range', () => {
  const trimmed = trimTrendToMeasuredWeeks([
    { week_start: '2026-06-01', sov_pct: null, total_count: 0, failure_count: 0 },
    { week_start: '2026-06-08', sov_pct: null, total_count: 0, failure_count: 12 },
    { week_start: '2026-06-15', sov_pct: 18, total_count: 150, failure_count: 0 },
  ])

  assert.equal(trimmed[0].week_start, '2026-06-08')
})

test('a week with only ambiguous results starts the measured range', () => {
  const trimmed = trimTrendToMeasuredWeeks([
    { week_start: '2026-06-01', sov_pct: null, total_count: 0, ambiguous_count: 0 },
    { week_start: '2026-06-08', sov_pct: null, total_count: 0, ambiguous_count: 7 },
    { week_start: '2026-06-15', sov_pct: 18, total_count: 150, ambiguous_count: 0 },
  ])

  assert.equal(trimmed[0].week_start, '2026-06-08')
})

test('a hospital measured for the first time this week keeps exactly one column', () => {
  const trimmed = trimTrendToMeasuredWeeks(
    Array.from({ length: 12 }, (_, index) =>
      index === 11
        ? { week_start: `w${index}`, sov_pct: 12, total_count: 30 }
        : { week_start: `w${index}`, sov_pct: null, total_count: 0 },
    ),
  )

  assert.deepEqual(trimmed.map((week) => week.week_start), ['w11'])
})

test('a hospital measured never gets an empty chart instead of twelve flat weeks', () => {
  const trimmed = trimTrendToMeasuredWeeks([
    { week_start: 'w0', sov_pct: null, total_count: 0 },
    { week_start: 'w1', sov_pct: null, total_count: 0 },
  ])

  assert.deepEqual(trimmed, [])
})
