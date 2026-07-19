import assert from 'node:assert/strict'
import test from 'node:test'

import {
  activationBlockers,
  buildPlatformVariants,
  canRunMeasurement,
  canSubmitSchedule,
  persistThenApprove,
  reportDeliveryBlockers,
} from './operator-safety.ts'

test('persistThenApprove saves visible fields before approval', async () => {
  const calls: string[] = []
  await persistThenApprove(
    async () => { calls.push('save') },
    async () => { calls.push('approve') },
  )
  assert.deepEqual(calls, ['save', 'approve'])
})

test('persistThenApprove does not approve when saving fails', async () => {
  let approved = false
  await assert.rejects(
    persistThenApprove(
      async () => { throw new Error('save failed') },
      async () => { approved = true },
    ),
    /save failed/,
  )
  assert.equal(approved, false)
})

test('buildPlatformVariants creates every question for every selected platform', () => {
  assert.deepEqual(
    buildPlatformVariants(['질문 1', '질문 2'], ['CHATGPT', 'GEMINI'], 'ko'),
    [
      { query_text: '질문 1', platform: 'CHATGPT', language: 'ko', is_active: true },
      { query_text: '질문 1', platform: 'GEMINI', language: 'ko', is_active: true },
      { query_text: '질문 2', platform: 'CHATGPT', language: 'ko', is_active: true },
      { query_text: '질문 2', platform: 'GEMINI', language: 'ko', is_active: true },
    ],
  )
})

test('buildPlatformVariants rejects unsupported platforms instead of silently scheduling them', () => {
  assert.throws(
    () => buildPlatformVariants(['질문'], ['PERPLEXITY'], 'ko'),
    /지원하지 않는 AI 서비스/,
  )
})

test('canRunMeasurement requires an active variant on an active target', () => {
  assert.equal(canRunMeasurement([{ status: 'ACTIVE', variants: [] }]), false)
  assert.equal(canRunMeasurement([{ status: 'PAUSED', variants: [{ is_active: true }] }]), false)
  assert.equal(canRunMeasurement([{ status: 'ACTIVE', variants: [{ is_active: false }] }]), false)
  assert.equal(canRunMeasurement([{ status: 'ACTIVE', variants: [{ is_active: true }] }]), true)
  assert.equal(canRunMeasurement([{
    status: 'ACTIVE',
    variants: [{ is_active: true, platform: 'PERPLEXITY' }],
  }]), false)
})

test('canSubmitSchedule remains blocked after the existing-schedule request fails', () => {
  assert.equal(canSubmitSchedule(true, null), false)
  assert.equal(canSubmitSchedule(false, 'GET failed'), false)
  assert.equal(canSubmitSchedule(false, null), true)
})

test('reportDeliveryBlockers includes missing PDF, review gaps, and medical risks', () => {
  const blockers = reportDeliveryBlockers({
    requireOperationalSummaries: true,
    hasPdf: false,
    hasSov: false,
    hasContent: true,
    hasStrategy: false,
    hasEssence: true,
    approvedPhilosophy: false,
    sourceCount: 2,
    processedSourceCount: 1,
    needsReviewCount: 1,
    missingStandardCount: 0,
    medicalRiskCount: 1,
  })
  assert.ok(blockers.some((item) => item.includes('PDF')))
  assert.ok(blockers.some((item) => item.includes('AI 답변')))
  assert.ok(blockers.some((item) => item.includes('운영 전략')))
  assert.ok(blockers.some((item) => item.includes('운영 기준')))
  assert.ok(blockers.some((item) => item.includes('병원 자료')))
  assert.ok(blockers.some((item) => item.includes('재검토')))
  assert.ok(blockers.some((item) => item.includes('의료광고')))
})

test('reportDeliveryBlockers lets a PDF-ready V0 diagnosis be delivered without monthly summaries', () => {
  assert.deepEqual(reportDeliveryBlockers({
    requireOperationalSummaries: false,
    hasPdf: true,
    hasSov: true,
    hasContent: false,
    hasStrategy: false,
    hasEssence: false,
    approvedPhilosophy: false,
    sourceCount: 0,
    processedSourceCount: 0,
    needsReviewCount: 0,
    missingStandardCount: 0,
    medicalRiskCount: 0,
  }), [])
})

test('activationBlockers allows default platform domain only after all prerequisites', () => {
  assert.deepEqual(activationBlockers({
    profile_complete: true,
    v0_report_done: true,
    site_built: true,
    schedule_set: true,
  }), [])
  assert.deepEqual(activationBlockers({
    profile_complete: true,
    v0_report_done: false,
    site_built: true,
    schedule_set: false,
  }), ['초기 진단 리포트', '콘텐츠 스케줄'])
})
