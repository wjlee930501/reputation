import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isInProgress,
  measuredWith,
  parseManualDiagnosisHistory,
} from './manual-diagnosis-history.ts'

function payload(overrides: Record<string, unknown> = {}) {
  return {
    items: [
      {
        id: 'diag-1',
        lead_id: 'lead-1',
        clinic_name: '교하본정형외과의원',
        specialty: '정형외과',
        region_keyword: '파주동패동',
        core_keywords: ['허리통증', '허리디스크'],
        execution_status: 'RUNNING',
        report_status: 'PENDING',
        delivery_status: 'INTERNAL',
        superseded_at: null,
        superseded_by_id: null,
        report_ready: false,
        created_at: '2026-09-22T10:21:27Z',
        ...overrides,
      },
    ],
  }
}

test('a created diagnosis shows what it was measured with', () => {
  const [row] = parseManualDiagnosisHistory(payload())
  assert.equal(row?.clinicName, '교하본정형외과의원')
  // 이 값이 곧 질의다 — 틀리면 보고서가 통째로 빗나가므로 화면이 그대로 보여줘야 한다.
  assert.equal(measuredWith(row!), '정형외과 · 파주동패동 · 허리통증, 허리디스크')
})

test('the report link only appears once the report is ready', () => {
  assert.equal(parseManualDiagnosisHistory(payload())[0]?.reportHref, null)
  const [ready] = parseManualDiagnosisHistory(
    payload({ report_status: 'READY', execution_status: 'SUCCEEDED', report_ready: true }),
  )
  assert.equal(
    ready?.reportHref,
    '/api/admin/leads/lead-1/diagnoses/diag-1/report',
  )
})

test('status wording matches the leads screen', () => {
  // 두 화면이 같은 진단을 두고 다른 말을 하면 어느 쪽을 믿어야 하는지 알 수 없다.
  const [row] = parseManualDiagnosisHistory(payload())
  assert.deepEqual(
    row?.badges.map((badge) => `${badge.axis} ${badge.label}`),
    ['측정 측정 중', '보고서 대기', '고객 발송 내부 보관'],
  )
  assert.match(row?.hint ?? '', /콜용 보고서를 만들고 있습니다/)
})

test('a superseded row says so and is not treated as in progress', () => {
  const [row] = parseManualDiagnosisHistory(
    payload({ superseded_at: '2026-09-22T11:00:00Z', superseded_by_id: 'diag-2' }),
  )
  assert.equal(row?.superseded, true)
  assert.match(row?.hint ?? '', /갈음/)
  // 갈음된 건을 진행 중으로 보면 목록이 영원히 새로고침을 돈다.
  assert.equal(isInProgress(row!), false)
})

test('an unfinished diagnosis is in progress so the list keeps refreshing', () => {
  assert.equal(isInProgress(parseManualDiagnosisHistory(payload())[0]!), true)
  const [done] = parseManualDiagnosisHistory(
    payload({ execution_status: 'SUCCEEDED', report_status: 'READY', report_ready: true }),
  )
  assert.equal(isInProgress(done!), false)
})

test('malformed rows are dropped instead of rendering a broken card', () => {
  assert.deepEqual(parseManualDiagnosisHistory({ items: [{ id: 'no-lead' }] }), [])
  assert.deepEqual(parseManualDiagnosisHistory({}), [])
  assert.deepEqual(parseManualDiagnosisHistory(null), [])
})

test('a missing clinic name does not render an empty row', () => {
  const [row] = parseManualDiagnosisHistory(payload({ clinic_name: null }))
  assert.equal(row?.clinicName, '병원명 확인 필요')
})
