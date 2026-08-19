import assert from 'node:assert/strict'
import test from 'node:test'

import {
  deriveHandoffDueStatus,
  deriveOnboardingSteps,
  deriveOnboardingSummary,
} from './onboarding-lifecycle.ts'

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
const acceptedHandoff = {
  state: 'HANDOFF_ACCEPTED',
  ae_owner_name: 'AE QA',
  sla_due_at: '2026-08-11T09:00:00Z',
}

test('onboarding follows the operator sequence and separates recurring outcomes', () => {
  const steps = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', acceptedHandoff)

  assert.deepEqual(
    steps.map(({ key, phase }) => ({ key, phase })),
    [
      { key: 'handoff', phase: 'onboarding' },
      { key: 'profile', phase: 'onboarding' },
      { key: 'v0', phase: 'onboarding' },
      { key: 'site', phase: 'onboarding' },
      { key: 'live', phase: 'onboarding' },
      { key: 'processing', phase: 'onboarding' },
      { key: 'philosophy_approved', phase: 'onboarding' },
      { key: 'schedule', phase: 'onboarding' },
      { key: 'first_publish', phase: 'post_onboarding' },
      { key: 'sov', phase: 'post_onboarding' },
    ],
  )
  assert.equal(deriveOnboardingSummary(steps, readiness).stateLabel, '정기 운영 중')
})

test('an unaccepted handoff is the first blocker and exposes one recovery action', () => {
  const steps = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', {
    ...acceptedHandoff,
    state: 'CONTRACTED',
  })
  const summary = deriveOnboardingSummary(steps, readiness)

  assert.equal(steps.find((step) => step.key === 'handoff')?.status, 'current')
  assert.equal(steps.find((step) => step.key === 'profile')?.status, 'completed')
  assert.equal(
    summary.blockedReason,
    '담당 AE가 인수 대기열에서 계약 정보와 처리 기한을 확인하고 인수를 승인해야 다음 단계로 진행할 수 있습니다.',
  )
  assert.equal(summary.nextActionHref, '/leads')
})

test('handoff due status uses plain Korean and distinguishes the next action', () => {
  const checkedAt = Date.parse('2026-08-11T10:00:00Z')

  assert.deepEqual(
    deriveHandoffDueStatus({ ...acceptedHandoff, state: 'CONTRACTED' }, checkedAt),
    { label: '처리 기한 지남', isOverdue: true },
  )
  assert.deepEqual(
    deriveHandoffDueStatus({ ...acceptedHandoff, state: 'HANDOFF_ACCEPTED' }, checkedAt),
    { label: '인수 완료', isOverdue: false },
  )
  assert.deepEqual(
    deriveHandoffDueStatus({ ...acceptedHandoff, state: 'CONTRACTED', sla_due_at: '2026-08-12T09:00:00Z' }, checkedAt),
    { label: '기한 내 진행 중', isOverdue: false },
  )
  assert.deepEqual(
    deriveHandoffDueStatus({ ...acceptedHandoff, state: 'CONTRACTED', sla_due_at: null }, checkedAt),
    { label: '처리 기한 확인 필요', isOverdue: false },
  )
})

test('LIVE is completed before content scheduling and recurring outcomes do not block onboarding completion', () => {
  const beforeSchedule = deriveOnboardingSteps(
    { ...hospital, schedule_set: false, site_live: true },
    sources,
    philosophies,
    readiness,
    'hospital-id',
    acceptedHandoff,
  )
  assert.equal(beforeSchedule.find((step) => step.key === 'schedule')?.status, 'current')
  assert.equal(beforeSchedule.find((step) => step.key === 'live')?.status, 'completed')

  const withoutOutcomes = deriveOnboardingSteps(
    hospital,
    sources,
    philosophies,
    { ...readiness, published_content_count: 0, sov_record_count: 0 },
    'hospital-id',
    acceptedHandoff,
  )
  assert.equal(deriveOnboardingSummary(withoutOutcomes, readiness).stateLabel, '온보딩 완료')
})

test('stale approved essence and partially processed included sources block readiness', () => {
  const steps = deriveOnboardingSteps(
    hospital,
    [...sources, { source_type: 'INTERVIEW', status: 'PENDING' }, { source_type: 'PHOTO_DOCTOR', status: 'PENDING' }],
    philosophies,
    { ...readiness, status: 'NEEDS_WORK', essence: { approved_philosophy_exists: true, source_stale: true } },
    'hospital-id',
    acceptedHandoff,
  )
  assert.equal(steps.find((step) => step.key === 'processing')?.status, 'current')
  assert.notEqual(steps.find((step) => step.key === 'philosophy_approved')?.status, 'completed')
})

test('later work stays completed when V0 is the current blocker', () => {
  const steps = deriveOnboardingSteps(
    { ...hospital, v0_report_done: false, site_built: false, site_live: false },
    sources,
    philosophies,
    readiness,
    'hospital-id',
    acceptedHandoff,
  )

  assert.equal(steps.find((step) => step.key === 'v0')?.status, 'current')
  assert.equal(steps.find((step) => step.key === 'processing')?.status, 'completed')
  assert.equal(steps.find((step) => step.key === 'philosophy_approved')?.status, 'completed')
  assert.equal(steps.find((step) => step.key === 'schedule')?.status, 'completed')
  assert.equal(
    deriveOnboardingSummary(steps, readiness).nextActionHref,
    '/hospitals/hospital-id/dashboard#v0-measurement-runs',
  )
})

test('the next-action CTA always points somewhere real', () => {
  const branches = [
    deriveOnboardingSummary(
      deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', acceptedHandoff),
      readiness,
    ),
    deriveOnboardingSummary(
      deriveOnboardingSteps({ ...hospital, schedule_set: false }, sources, philosophies, readiness, 'hospital-id', acceptedHandoff),
      readiness,
    ),
  ]

  for (const summary of branches) {
    assert.notEqual(summary.nextActionHref, '#', `${summary.stateLabel}: dead link`)
    if (summary.nextActionHref !== null) {
      assert.ok(
        summary.nextActionHref.startsWith('/hospitals/') || summary.nextActionHref === '/leads',
        `${summary.stateLabel}: unexpected href ${summary.nextActionHref}`,
      )
    }
  }
})
