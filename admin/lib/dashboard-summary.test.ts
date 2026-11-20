import assert from 'node:assert/strict'
import test from 'node:test'

import { summarizeDashboard, todoLines } from './dashboard-summary.ts'

const live = { kind: 'live', remaining: [] }
const hospitals = [
  { id: 'a', name: '가', status: 'ACTIVE', open_exception_count: 2, visual_approval_missing: [], public_service_state: live },
  { id: 'b', name: '나', status: 'ONBOARDING', open_exception_count: 0, visual_approval_missing: ['로고'], public_service_state: { kind: 'not_live', remaining: ['address', 'phone'] } },
  { id: 'c', name: '다', status: 'PAUSED', open_exception_count: 0, visual_approval_missing: ['로고'], public_service_state: { kind: 'paused', remaining: [] } },
]
const metric = (id: string, pub: number, plan: number) => ({
  hospital_id: id, year: 2026, month: 11, published_count: pub, public_count: pub, planned_total: plan,
  mention_rate: null, mention_rate_measured_at: null,
})
const reports = { period_month: 10, missing: [{ hospital_id: 'b' }], undelivered: [{ hospital_id: 'a' }] }
const counts = { operator_todo_groups: 1, hospital_count: 3, lead_total: 17, lead_needs_attention: 2 }

test('dashboard numbers read server states and keep the to-do count apart from other notices', () => {
  const metrics = new Map([['a', metric('a', 6, 12)], ['b', metric('b', 0, 0)], ['c', metric('c', 5, 12)]])
  const summary = summarizeDashboard(hospitals, metrics, reports, counts)
  assert.equal(summary.live, 1)
  assert.equal(summary.preparing, 1)
  assert.equal(summary.paused, 1)
  assert.equal(summary.live + summary.preparing + summary.paused, summary.total)
  // 일시 정지 병원은 공개 페이지 항목·이번 달 발행 합계에 넣지 않는다.
  assert.equal(summary.visualMissingHospitals, 1)
  assert.deepEqual([summary.monthPublic, summary.monthPlanned], [6, 12])
  // 처리할 일은 큐의 원인 묶음 수 그대로 — 병원별 예외 합(2)과 다를 수 있다.
  assert.equal(summary.operatorTodo, 1)
  assert.equal(summary.reportGapHospitals, 2)
  assert.equal(summarizeDashboard(hospitals, metrics, null, null).operatorTodo, null)
  // 보조 호출을 아직 못 받았거나 실패하면 0이 아니라 null(「—」).
  const pending = summarizeDashboard(hospitals, null, null, null)
  assert.deepEqual([pending.reportGapHospitals, pending.monthPublic, pending.monthPlanned], [null, null, null])
})

test('today lines put exceptions first, reports before page items, paused hospitals last', () => {
  const lines = todoLines(hospitals, reports)
  assert.deepEqual(lines.map((l) => l.key), [
    'a:exception', 'a:report', 'b:report', 'b:visual', 'b:preparing', 'c:paused',
  ])
  assert.equal(lines[1].title, '10월 원장 보고 미전달')
  assert.equal(lines[2].title, '10월 원장 보고 미생성')
})

test('a later hospital report is not hidden behind an earlier hospital page item', () => {
  const visualFirst = [
    { id: 'v', name: '가', status: 'ACTIVE', open_exception_count: 0, visual_approval_missing: ['로고'], public_service_state: live },
    { id: 'r', name: '나', status: 'ACTIVE', open_exception_count: 0, visual_approval_missing: [], public_service_state: live },
  ]
  const lines = todoLines(visualFirst, { period_month: 10, missing: [], undelivered: [{ hospital_id: 'r' }] })
  assert.deepEqual(lines.map((l) => l.key), ['r:report', 'v:visual'])
})
