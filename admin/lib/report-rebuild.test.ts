import assert from 'node:assert/strict'
import test from 'node:test'

import {
  REPORT_REBUILD_DELIVERED_WARNING,
  reportRebuildPlan,
} from './report-rebuild.ts'
import { parseReport } from './report-review.ts'

// 2026-08-10 12:00 KST — 2026년 7월은 마감됐고 8월은 아직이다.
const NOW = new Date('2026-08-10T03:00:00Z')

test('a finished monthly report can be rebuilt from the list row', () => {
  // Given: 정상적으로 만들어졌고 아직 전달하지 않은 지난달 보고서
  const plan = reportRebuildPlan(
    { periodYear: 2026, periodMonth: 7, initialReport: false, delivered: false },
    NOW,
  )

  // Then: 실패하지 않았어도 다시 만들 수 있고, 경고할 것은 없다
  assert.equal(plan.kind, 'available')
  assert.equal(plan.kind === 'available' ? plan.warning : 'unexpected', null)
})

test('a delivered report stays rebuildable but says the new version must be re-sent', () => {
  const plan = reportRebuildPlan(
    { periodYear: 2026, periodMonth: 7, initialReport: false, delivered: true },
    NOW,
  )

  assert.equal(plan.kind, 'available')
  assert.equal(
    plan.kind === 'available' ? plan.warning : null,
    REPORT_REBUILD_DELIVERED_WARNING,
  )
  // 전달 기록이 지워진다고 읽히면 AE가 누르지 못한다 — 보존을 명시한다.
  assert.match(REPORT_REBUILD_DELIVERED_WARNING, /그대로 남으며/)
})

test('the initial diagnosis is not rebuilt through the monthly path', () => {
  const plan = reportRebuildPlan(
    { periodYear: 2026, periodMonth: 7, initialReport: true, delivered: false },
    NOW,
  )

  assert.equal(plan.kind, 'unavailable')
  assert.match(plan.kind === 'unavailable' ? plan.reason : '', /초기 진단/)
})

test('an unclosed period is refused with the reason instead of a server error', () => {
  const plan = reportRebuildPlan(
    { periodYear: 2026, periodMonth: 8, initialReport: false, delivered: false },
    NOW,
  )

  assert.equal(plan.kind, 'unavailable')
  assert.match(plan.kind === 'unavailable' ? plan.reason : '', /마감되지 않은 달/)
})

test('a V0 row from the server is refused even though the server marks it delivery-tracked', () => {
  // Given: 서버는 V0에도 delivery_tracked=true를 보낸다(backend reports.py 직렬화).
  // 이 값으로 V0를 가르면 V0 행에 버튼이 붙고, 누르면 그 달의 월간 보고서가 생긴다.
  const v0 = parseReport({
    id: 'v0-1',
    hospital_id: 'hospital-1',
    period_year: 2026,
    period_month: 7,
    report_type: 'V0',
    delivery_tracked: true,
  })
  const monthly = parseReport({
    id: 'monthly-1',
    hospital_id: 'hospital-1',
    period_year: 2026,
    period_month: 7,
    report_type: 'MONTHLY',
    delivery_tracked: true,
  })
  assert.ok(v0 && monthly)

  const plan = (report: NonNullable<typeof v0>) =>
    reportRebuildPlan(
      {
        periodYear: report.periodYear,
        periodMonth: report.periodMonth,
        initialReport: report.isInitialReport,
        delivered: false,
      },
      NOW,
    )

  assert.equal(plan(v0).kind, 'unavailable')
  assert.equal(plan(monthly).kind, 'available')
})
