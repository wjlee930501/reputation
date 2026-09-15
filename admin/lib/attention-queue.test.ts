import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ATTENTION_VISIBLE_ROWS,
  type AttentionQueue,
  hasReportGaps,
  hiddenReportCount,
  reportGapSummary,
} from './attention-queue.ts'

function queue(overrides: Partial<AttentionQueue> = {}): AttentionQueue {
  return {
    unreviewed_total: 0,
    overdue_total: 0,
    overdue_hours: 24,
    withheld_total: 0,
    hospitals: [],
    ...overrides,
  }
}

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

test('the queue stays hidden when no report is missing', () => {
  assert.equal(hasReportGaps(null), false)
  assert.equal(hasReportGaps(queue()), false)
  assert.equal(hasReportGaps(queue({ reports: reports(0, 0) })), false)
  assert.equal(hasReportGaps(queue({ reports: reports(1, 0) })), true)
  assert.equal(hasReportGaps(queue({ reports: reports(0, 1) })), true)
})

test('the post-publish sample never opens the queue on its own', () => {
  // 공개 후 확인 표본은 발행을 막지 않는 관측용 표본이라 운영자 큐가 아니다(B1).
  // 응답에는 남아 있지만 목록 상단은 원장 보고만 보고 뜬다.
  const samplesOnly = queue({
    unreviewed_total: 7,
    overdue_total: 3,
    withheld_total: 2,
    hospitals: [
      {
        hospital_id: 'h1',
        hospital_name: '표본 의원',
        unreviewed_count: 7,
        overdue_count: 3,
        oldest_published_at: '2026-08-01T00:00:00Z',
        withheld_count: 2,
      },
    ],
  })
  assert.equal(hasReportGaps(samplesOnly), false)
  assert.equal(hasReportGaps({ ...samplesOnly, reports: reports(1, 0) }), true)
})

test('hiddenReportCount only counts rows beyond the visible window', () => {
  assert.equal(hiddenReportCount(reports(1, 1)), 0)
  assert.equal(hiddenReportCount(reports(ATTENTION_VISIBLE_ROWS, 0)), 0)
  assert.equal(hiddenReportCount(reports(ATTENTION_VISIBLE_ROWS, 3)), 3)
  assert.equal(hiddenReportCount(reports(0, 0)), 0)
})

test('reportGapSummary keeps the two states apart', () => {
  // 할 일이 다르다 — 하나는 다시 만들기, 하나는 원장에게 보내기.
  assert.equal(reportGapSummary(reports(2, 3)), '미생성 2곳 · 미전달 3곳')
  assert.equal(reportGapSummary(reports(2, 0)), '미생성 2곳')
  assert.equal(reportGapSummary(reports(0, 3)), '미전달 3곳')
  assert.equal(reportGapSummary(reports(0, 0)), '')
})
