import assert from 'node:assert/strict'
import test from 'node:test'

import { deriveOnboardingSteps, deriveOnboardingSummary } from './onboarding-lifecycle.ts'

const hospital = {
  profile_complete: true,
  v0_report_done: true,
  site_built: true,
  site_live: true,
  schedule_set: true,
}
const sources = [{ source_type: 'HOMEPAGE', status: 'PROCESSED' }]
const philosophies = [{ status: 'APPROVED' }]
const readiness = {
  status: 'READY',
  published_content_count: 1,
  sov_record_count: 2,
  essence: { approved_philosophy_exists: true, source_stale: false },
  checks: [
    'core_profile', 'v0_report', 'site_built', 'domain', 'essence_sources', 'essence_freshness',
    'schedule', 'published_content', 'sov_data',
  ].map((key) => ({ key, passed: true })),
}

test('onboarding only completes after LIVE and every operational hard gate', () => {
  const steps = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id')
  assert.equal(steps.every((step) => step.status === 'completed'), true)
  assert.equal(deriveOnboardingSummary(steps, readiness).stateLabel, '운영 준비 완료')
})

test('schedule is an operational gate after LIVE, not an activation prerequisite', () => {
  const steps = deriveOnboardingSteps({ ...hospital, schedule_set: false }, sources, philosophies, readiness, 'hospital-id')
  assert.equal(steps.find((step) => step.key === 'live')?.status, 'completed')
  assert.equal(steps.find((step) => step.key === 'schedule')?.status, 'current')
})

test('stale approved essence and partially processed included sources block readiness', () => {
  const steps = deriveOnboardingSteps(
    hospital,
    [...sources, { source_type: 'INTERVIEW', status: 'PENDING' }, { source_type: 'PHOTO_DOCTOR', status: 'PENDING' }],
    philosophies,
    { ...readiness, status: 'NEEDS_WORK', essence: { approved_philosophy_exists: true, source_stale: true } },
    'hospital-id',
  )
  assert.equal(steps.find((step) => step.key === 'processing')?.status, 'current')
  assert.notEqual(steps.find((step) => step.key === 'philosophy_approved')?.status, 'completed')
})

test('completed-looking flags are not reported ready when backend readiness is not READY', () => {
  const needsWork = { ...readiness, status: 'NEEDS_WORK' }
  const steps = deriveOnboardingSteps(hospital, sources, philosophies, needsWork, 'hospital-id')
  const summary = deriveOnboardingSummary(steps, needsWork)
  assert.equal(summary.stateLabel, '검증 필요')
})
