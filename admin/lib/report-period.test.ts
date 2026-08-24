import assert from 'node:assert/strict'
import test from 'node:test'

import {
  currentMonthValue,
  opsRelevantMonthValue,
  parseMonthValue,
  previousMonthValue,
  reportMonthBlockReason,
  reportMonthOptions,
  reportYearOptions,
} from './report-period.ts'

// 입력은 전부 UTC 절대시각이다 — 실행 환경의 로컬 시간대와 무관하게 같은 결과가 나와야
// 한다(백엔드가 Asia/Seoul로 기간을 계산하므로 화면 기본값도 KST를 따라야 한다).

test('previousMonthValue returns the month before, in KST', () => {
  assert.equal(previousMonthValue(new Date('2026-08-04T03:00:00Z')), '2026-07')
  assert.equal(previousMonthValue(new Date('2026-03-31T12:00:00Z')), '2026-02')
})

test('previousMonthValue rolls back across the year boundary', () => {
  // 1월에 "지난달"은 전년 12월이다 — 여기가 틀리면 복구 요청이 엉뚱한 달을 만든다.
  assert.equal(previousMonthValue(new Date('2026-01-02T00:00:00Z')), '2025-12')
})

test('previousMonthValue follows KST even when UTC is still the previous day', () => {
  // 2026-07-31T15:30Z == 2026-08-01 00:30 KST. 로컬(UTC) 기준으로 계산하면 6월이 나온다.
  assert.equal(previousMonthValue(new Date('2026-07-31T15:30:00Z')), '2026-07')
  // 연 경계에서도 같은 함정: 2025-12-31T15:30Z == 2026-01-01 00:30 KST.
  assert.equal(previousMonthValue(new Date('2025-12-31T15:30:00Z')), '2025-12')
})

test('previousMonthValue zero-pads single-digit months', () => {
  assert.equal(previousMonthValue(new Date('2026-06-15T03:00:00Z')), '2026-05')
  assert.equal(previousMonthValue(new Date('2026-02-01T03:00:00Z')), '2026-01')
})

test('parseMonthValue accepts the input element format', () => {
  assert.deepEqual(parseMonthValue('2026-07'), { year: 2026, month: 7 })
  assert.deepEqual(parseMonthValue(' 2025-12 '), { year: 2025, month: 12 })
})

test('parseMonthValue rejects malformed or impossible months', () => {
  for (const bad of ['', '2026', '2026-7', '2026-13', '2026-00', 'abcd-01', '2026-07-01']) {
    assert.equal(parseMonthValue(bad), null, `${bad} must not parse`)
  }
})

test('currentMonthValue follows KST across the day and year boundary', () => {
  assert.equal(currentMonthValue(new Date('2026-08-22T03:00:00Z')), '2026-08')
  // 2026-07-31T15:30Z == 2026-08-01 00:30 KST.
  assert.equal(currentMonthValue(new Date('2026-07-31T15:30:00Z')), '2026-08')
  assert.equal(currentMonthValue(new Date('2025-12-31T15:30:00Z')), '2026-01')
})

test('report picker follows the latest real report instead of a stale calendar default', () => {
  assert.equal(opsRelevantMonthValue([
    { periodYear: 2026, periodMonth: 7 },
    { periodYear: 2026, periodMonth: 8 },
    { periodYear: 2025, periodMonth: 12 },
  ], new Date('2026-08-24T02:00:00Z')), '2026-08')
  assert.equal(opsRelevantMonthValue([], new Date('2026-08-24T02:00:00Z')), '2026-07')
})

// A-8: 2026-08-22에 8월이 목록에서 아예 사라져 "8월 리포트를 만들 수 없다"는 사실만
// 남고 이유는 어디에도 없었다. 이번 달은 남기고 잠근다.
test('the current month stays in the picker, locked with its reason', () => {
  const now = new Date('2026-08-22T03:00:00Z')
  const options = reportMonthOptions(2026, now)

  assert.deepEqual(options.map((option) => option.month), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])

  const august = options.find((option) => option.month === 8)
  assert.equal(august?.selectable, false)
  assert.equal(august?.reason, 'NOT_CLOSED')
  assert.equal(august?.label, '8월 (마감 후 생성)')
})

test('closed months are selectable and future months are locked apart from the current one', () => {
  const now = new Date('2026-08-22T03:00:00Z')
  const options = reportMonthOptions(2026, now)

  assert.deepEqual(
    options.filter((option) => option.selectable).map((option) => option.month),
    [1, 2, 3, 4, 5, 6, 7],
  )
  assert.deepEqual(
    options.filter((option) => option.reason === 'FUTURE').map((option) => option.month),
    [9, 10, 11, 12],
  )
})

test('a month unlocks exactly at its 00:15 KST close boundary', () => {
  // 2026-08-31T15:00Z == 2026-09-01 00:00 KST — 마감 15분 전.
  assert.equal(reportMonthBlockReason({ year: 2026, month: 8 }, new Date('2026-08-31T15:00:00Z')), 'NOT_CLOSED')
  // 2026-08-31T15:15Z == 2026-09-01 00:15 KST — 마감.
  assert.equal(reportMonthBlockReason({ year: 2026, month: 8 }, new Date('2026-08-31T15:15:00Z')), null)
})

test('December closes into the next year rather than month 13', () => {
  // 2025-12-31T15:00Z == 2026-01-01 00:00 KST — 마감 15분 전.
  assert.equal(reportMonthBlockReason({ year: 2025, month: 12 }, new Date('2025-12-31T15:00:00Z')), 'NOT_CLOSED')
  // 2025-12-31T15:16Z == 2026-01-01 00:16 KST — 마감 후.
  assert.equal(reportMonthBlockReason({ year: 2025, month: 12 }, new Date('2025-12-31T15:16:00Z')), null)
})

test('every past year offers all twelve months', () => {
  const options = reportMonthOptions(2025, new Date('2026-08-22T03:00:00Z'))
  assert.equal(options.length, 12)
  assert.ok(options.every((option) => option.selectable))
})

// 1월에는 "지난달"이 전년 12월이라, 상한을 지난달로 잡으면 올해가 목록에서 사라졌다.
test('the year list reaches the current KST year even in January', () => {
  assert.equal(reportYearOptions(new Date('2026-01-05T03:00:00Z'))[0], 2026)
  assert.equal(reportYearOptions(new Date('2026-08-22T03:00:00Z'))[0], 2026)
  assert.equal(reportYearOptions(new Date('2026-08-22T03:00:00Z')).at(-1), 2025)
})
