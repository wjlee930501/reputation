import assert from 'node:assert/strict'
import test from 'node:test'

import { parseMonthValue, previousMonthValue } from './report-period.ts'

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
