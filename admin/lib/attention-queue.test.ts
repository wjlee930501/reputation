import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ATTENTION_VISIBLE_ROWS,
  type AttentionQueue,
  formatWaiting,
  hasAttentionWork,
  hasReportGaps,
  hiddenHospitalCount,
  reportGapSummary,
} from './attention-queue.ts'

const NOW = new Date('2026-08-04T12:00:00Z')

function queue(overrides: Partial<AttentionQueue> = {}): AttentionQueue {
  return {
    unreviewed_total: 0,
    overdue_total: 0,
    overdue_hours: 24,
    hospitals: [],
    ...overrides,
  }
}

test('formatWaiting reports hours within the first day and days after', () => {
  assert.equal(formatWaiting('2026-08-04T09:00:00Z', NOW), '3시간째')
  assert.equal(formatWaiting('2026-08-03T11:00:00Z', NOW), '1일째')
  assert.equal(formatWaiting('2026-07-30T12:00:00Z', NOW), '5일째')
})

test('formatWaiting collapses anything under an hour to 방금', () => {
  assert.equal(formatWaiting('2026-08-04T11:30:00Z', NOW), '방금')
})

test('formatWaiting yields nothing it cannot compute', () => {
  // 값이 없거나 시계가 어긋나 음수가 나오면 "0시간째" 같은 거짓 정보를 만들지 않는다.
  assert.equal(formatWaiting(null, NOW), '')
  assert.equal(formatWaiting('not-a-date', NOW), '')
  assert.equal(formatWaiting('2026-08-04T13:00:00Z', NOW), '')
})

test('the queue stays hidden when there is nothing to confirm', () => {
  assert.equal(hasAttentionWork(null), false)
  assert.equal(hasAttentionWork(queue()), false)
  assert.equal(hasAttentionWork(queue({ unreviewed_total: 1 })), true)
})

test('hiddenHospitalCount only counts rows beyond the visible window', () => {
  const rows = (n: number) =>
    queue({
      hospitals: Array.from({ length: n }, (_, i) => ({
        hospital_id: `h${i}`,
        hospital_name: `병원 ${i}`,
        unreviewed_count: 1,
        overdue_count: 0,
        oldest_published_at: null,
      })),
    })

  assert.equal(hiddenHospitalCount(rows(2)), 0)
  assert.equal(hiddenHospitalCount(rows(ATTENTION_VISIBLE_ROWS)), 0)
  assert.equal(hiddenHospitalCount(rows(ATTENTION_VISIBLE_ROWS + 3)), 3)
})

const reports = (missing: number, undelivered: number) => ({
  period_year: 2026,
  period_month: 7,
  missing: Array.from({ length: missing }, (_, i) => ({
    hospital_id: `m${i}`, hospital_name: `미생성 ${i}`, report_id: null,
  })),
  undelivered: Array.from({ length: undelivered }, (_, i) => ({
    hospital_id: `u${i}`, hospital_name: `미전달 ${i}`, report_id: `r${i}`,
  })),
})

test('report gaps alone are enough to show the queue', () => {
  // 확인 대기가 0이어도 지난달 리포트가 밀렸으면 할 일이 남은 것이다.
  assert.equal(hasAttentionWork(queue({ reports: reports(1, 0) })), true)
  assert.equal(hasAttentionWork(queue({ reports: reports(0, 1) })), true)
  assert.equal(hasAttentionWork(queue({ reports: reports(0, 0) })), false)
})

test('hasReportGaps ignores a queue that has none', () => {
  assert.equal(hasReportGaps(null), false)
  assert.equal(hasReportGaps(queue()), false)
  assert.equal(hasReportGaps(queue({ reports: reports(0, 0) })), false)
  assert.equal(hasReportGaps(queue({ reports: reports(2, 1) })), true)
})

test('reportGapSummary keeps the two states apart', () => {
  // 할 일이 다르다 — 하나는 다시 만들기, 하나는 원장에게 보내기.
  assert.equal(reportGapSummary(reports(2, 3)), '미생성 2곳 · 미전달 3곳')
  assert.equal(reportGapSummary(reports(2, 0)), '미생성 2곳')
  assert.equal(reportGapSummary(reports(0, 3)), '미전달 3곳')
  assert.equal(reportGapSummary(reports(0, 0)), '')
})
