import assert from 'node:assert/strict'
import test from 'node:test'

import {
  deliveryConflict,
  deliveryEventLabel,
  getDoctorDownload,
  getInternalReportLabel,
  isEffectivelyDelivered,
  latestDeliveryEvent,
  readReportDeliveryState,
  reportListDeveloperNote,
  reportStatusLabel,
  reportSummaryCounts,
  shouldShowDeliveryProblem,
} from './report-delivery.ts'

test('server delivery readiness is the only positive authority', () => {
  assert.deepEqual(readReportDeliveryState({ deliveryReady: true, deliveryBlockers: [] }), { ready: true, blockers: [] })
  assert.equal(readReportDeliveryState({ deliveryReady: false, deliveryBlockers: [] }).ready, false)
  assert.equal(readReportDeliveryState({ deliveryReady: true, deliveryBlockers: ['자료 변경'] }).ready, false)
})

test('only the validated doctor artifact becomes the primary download', () => {
  assert.equal(
    getDoctorDownload('h1', 'r1', 'VALID', { deliveryReady: true, effectiveEventType: null, sentAt: null }),
    '/api/admin/hospitals/h1/reports/r1/download?audience=doctor',
  )
  assert.equal(
    getDoctorDownload('h1', 'r1', 'VALID', { deliveryReady: false, effectiveEventType: 'DELIVERED', sentAt: '2026-08-01T00:00:00Z' }),
    '/api/admin/hospitals/h1/reports/r1/download?audience=doctor',
  )
  assert.equal(
    getDoctorDownload('h1', 'r1', 'INVALID', { deliveryReady: true, effectiveEventType: null, sentAt: null }),
    null,
  )
  assert.equal(
    getDoctorDownload('h1', 'r1', 'VALID', { deliveryReady: false, effectiveEventType: null, sentAt: null }),
    null,
  )
  assert.equal(getInternalReportLabel(true, true), '내부 검수용 리포트 열기 · 원장 전달 금지')
})

test('rescission overrides compatibility sent time until re-delivery', () => {
  assert.equal(isEffectivelyDelivered({ effectiveEventType: 'RESCINDED', sentAt: '2026-08-10' }), false)
  assert.equal(isEffectivelyDelivered({ effectiveEventType: 'REDELIVERED', sentAt: '2026-08-10' }), true)
})

test('stale readiness conflicts become problem, impact, and exact action without raw jargon', () => {
  const issue = deliveryConflict({ code: 'current_readiness_blocked', blockers: ['병원 자료가 변경됐습니다.'] })
  assert.equal(issue.problem, '병원 자료가 변경됐습니다.')
  assert.match(issue.customerImpact, /전달할 수 없습니다/)
  assert.equal(issue.action, 'operations')
  assert.doesNotMatch(JSON.stringify(issue), /SLA|CUSTOMER_READY|manifest|current_readiness_blocked/)
})

test('delivery history never renders raw event enums', () => {
  assert.equal(deliveryEventLabel('DELIVERED'), '최초 전달 기록')
  assert.equal(deliveryEventLabel('CORRECTED'), '전달 정보 수정 기록')
  assert.equal(deliveryEventLabel('RESCINDED'), '전달 기록 무효 처리')
  assert.equal(deliveryEventLabel('REDELIVERED'), '다시 전달한 기록')
})

test('the last item in the ascending API delivery history is the current receipt', () => {
  const earlier = { id: 'first', type: 'DELIVERED' as const, recipient: '김 원장', channel: '대면', operator: '담당자', note: null, reason: null, createdAt: '2026-08-01T00:00:00Z' }
  const latest = { ...earlier, id: 'second', type: 'CORRECTED' as const, createdAt: '2026-08-02T00:00:00Z' }

  assert.equal(latestDeliveryEvent([earlier, latest])?.id, 'second')
})

test('list summary partitions delivered, ready, and blocked reports exactly once', () => {
  const reports = [
    { deliveryReady: true, deliveryBlockers: [], effectiveEventType: 'DELIVERED', sentAt: '2026-08-01' },
    { deliveryReady: true, deliveryBlockers: [], effectiveEventType: null, sentAt: null },
    { deliveryReady: false, deliveryBlockers: ['자료 확인 필요'], effectiveEventType: null, sentAt: null },
  ]

  assert.deepEqual(reportSummaryCounts(reports), { delivered: 1, ready: 1, blocked: 1 })
})

test('the initial diagnosis report is never described with the monthly delivery story', () => {
  // 초기 진단(V0)에는 검증본에 묶인 전달 기록이 없다. 그런데도 리포트 화면이 월간과
  // 같은 게이트로 판정해 통과할 수 없는 조건(`검증된 원장 보고용 PDF`)을 요구했고,
  // 온보딩 3단계는 완료인데 리포트만 조치 필요로 남는 모순이 생겼다(A-7).
  const v0Ready = {
    deliveryTracked: false,
    deliveryReady: true,
    deliveryBlockers: [],
    effectiveEventType: null,
    sentAt: null,
  }
  assert.equal(reportStatusLabel(v0Ready), '원장 보고 자료 준비 완료')
  assert.equal(shouldShowDeliveryProblem(v0Ready), false)

  const v0Blocked = { ...v0Ready, deliveryReady: false, deliveryBlockers: ['초기 진단 PDF가 아직 만들어지지 않았습니다.'] }
  assert.equal(reportStatusLabel(v0Blocked), '조치 필요')
  assert.equal(shouldShowDeliveryProblem(v0Blocked), true)

  // 월간은 기존 서사를 그대로 지킨다.
  const monthlyDelivered = {
    deliveryTracked: true,
    deliveryReady: true,
    deliveryBlockers: [],
    effectiveEventType: 'DELIVERED',
    sentAt: '2026-08-01',
  }
  assert.equal(reportStatusLabel(monthlyDelivered), '전달 기록 있음')
  assert.equal(reportStatusLabel({ ...monthlyDelivered, effectiveEventType: null, sentAt: null }), '전달 전 검수 가능')
})

test('a V0 report never counts as a delivery receipt even if a legacy sent_at exists', () => {
  // 전달 기록을 남기지 않는 리포트가 '전달 기록 있음'으로 집계되면 요약 카드가
  // 있지도 않은 전달을 보고하게 된다.
  const reports = [
    { deliveryTracked: false, deliveryReady: true, deliveryBlockers: [], effectiveEventType: null, sentAt: '2026-08-01' },
  ]
  assert.deepEqual(reportSummaryCounts(reports), { delivered: 0, ready: 1, blocked: 0 })
})

test('page-level developer copy works without a selected report and contains no raw failure', () => {
  const note = reportListDeveloperNote('hospital-1', new Date('2026-08-10T00:00:00Z'))

  assert.match(note, /hospital-1/)
  assert.match(note, /2026-08-10T00:00:00.000Z/)
  assert.doesNotMatch(note, /SLA|CUSTOMER_READY|payload|raw_response/)
})
