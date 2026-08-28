import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  countPendingPhilosophyDrafts,
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
  // 공개 표면 시각 승인 네 항목. 프로파일 단계 완료 판정에 함께 들어간다.
  // 업로드된 자산 참조여야 공개 화면이 실제로 그린다 — 외부 주소는 승인으로 치지 않는다.
  logo_url: 'gs://reputation-images/assets/abc/clinic-logo.png',
  brand_primary_color: '#0d5bd1',
  hero_headline: '동네 주민의 일상을 지키는 진료',
  site_access_mode: 'appointment',
}
const sources = [{ source_type: 'HOMEPAGE', status: 'PROCESSED', raw_text: '병원 진료 근거' }]
const philosophies = [{ status: 'APPROVED' }]
const readiness = {
  status: 'READY',
  published_content_count: 1,
  sov_record_count: 2,
  report_count: 1,
  v0_report_pdf_count: 1,
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
  assert.equal(
    steps.find((step) => step.key === 'processing')?.href,
    '/hospitals/hospital-id/onboarding#step-5',
  )
  assert.equal(deriveOnboardingSummary(steps, readiness).stateLabel, '정기 운영 중')
})

// KEEP-8: 온보딩은 8단계다. 단계를 추가·합치거나 순서를 바꾸면 운영자가 외운 흐름과
// 화면의 "온보딩 N / 8" 표기가 동시에 깨진다. 새 요구사항은 기존 단계 안에서 처리한다.
test('onboarding stays at exactly eight steps — never a ninth', () => {
  const steps = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', acceptedHandoff)
  const onboarding = steps.filter((step) => step.phase === 'onboarding')

  assert.equal(onboarding.length, 8)
  assert.equal(steps.filter((step) => step.phase === 'post_onboarding').length, 2)
})

test('the onboarding order is fixed and its indexes stay contiguous from zero', () => {
  const steps = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', acceptedHandoff)
  const onboarding = steps.filter((step) => step.phase === 'onboarding')

  assert.deepEqual(
    onboarding.map((step) => step.key),
    ['handoff', 'profile', 'v0', 'site', 'live', 'processing', 'philosophy_approved', 'schedule'],
  )
  assert.deepEqual(
    onboarding.map((step) => step.index),
    [0, 1, 2, 3, 4, 5, 6, 7],
  )
  // 후속 성과는 온보딩 8단계 뒤에 붙고, 그 안으로 끼어들지 않는다.
  assert.deepEqual(
    steps.filter((step) => step.phase === 'post_onboarding').map((step) => step.index),
    [8, 9],
  )
})

test('the hub owns the onboarding sequence and step keys never duplicate', () => {
  const steps = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', acceptedHandoff)

  assert.equal(new Set(steps.map((step) => step.key)).size, steps.length)
  // 자료 수집·처리는 허브 안에서 항상 펼쳐지는 6번째 단계다.
  assert.equal(
    steps.find((step) => step.key === 'processing')?.index,
    5,
  )
})

test('the derived step order does not depend on which flags are already done', () => {
  const fresh = deriveOnboardingSteps(
    { profile_complete: false, v0_report_done: false, site_built: false, site_live: false, schedule_set: false },
    [],
    [],
    null,
    'hospital-id',
    null,
  )
  const finished = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', acceptedHandoff)

  assert.deepEqual(
    fresh.map((step) => step.key),
    finished.map((step) => step.key),
  )
  assert.equal(fresh.filter((step) => step.phase === 'onboarding').length, 8)
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
  const completedSummary = deriveOnboardingSummary(withoutOutcomes, readiness)
  assert.equal(completedSummary.stateLabel, '온보딩 완료')
  assert.equal(completedSummary.nextActionHref, null)
  assert.equal(completedSummary.blockedReason, null)
  assert.doesNotMatch(completedSummary.detail, /다음 (후속 )?작업/)
})

test('8/8 never leaves a next-action CTA even when post-onboarding outcomes are complete', () => {
  const steps = deriveOnboardingSteps(
    hospital,
    sources,
    philosophies,
    readiness,
    'hospital-id',
    acceptedHandoff,
  )
  const summary = deriveOnboardingSummary(steps, readiness)

  assert.equal(steps.filter((step) => step.phase === 'onboarding' && step.status === 'completed').length, 8)
  assert.equal(summary.nextActionHref, null)
  assert.equal(summary.nextActionLabel, '')
  assert.equal(summary.blockedReason, null)
})

test('stale approved essence and partially processed included sources block readiness', () => {
  const steps = deriveOnboardingSteps(
    hospital,
    [...sources, { source_type: 'INTERVIEW', status: 'PENDING', raw_text: '원장 인터뷰' }, { source_type: 'PHOTO_DOCTOR', status: 'PENDING' }],
    philosophies,
    { ...readiness, status: 'NEEDS_WORK', essence: { approved_philosophy_exists: true, source_stale: true } },
    'hospital-id',
    acceptedHandoff,
  )
  assert.equal(steps.find((step) => step.key === 'processing')?.status, 'current')
  assert.notEqual(steps.find((step) => step.key === 'philosophy_approved')?.status, 'completed')
})

test('raw-text-less pending rows and photos do not satisfy the evidence source requirement', () => {
  for (const incompleteSources of [
    [{ source_type: 'HOMEPAGE', status: 'PENDING' }],
    [{ source_type: 'HOMEPAGE', status: 'PENDING', raw_text: '   ' }],
    [{ source_type: 'PHOTO_DOCTOR', status: 'PROCESSED', raw_text: 'photo metadata' }],
  ]) {
    const steps = deriveOnboardingSteps(
      hospital,
      incompleteSources,
      philosophies,
      readiness,
      'hospital-id',
      acceptedHandoff,
    )

    assert.notEqual(steps.find((step) => step.key === 'processing')?.status, 'completed')
  }
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

// B-1: 프로파일 단계 안의 시각 승인이 남아 있는데도 단계가 완료로 보이면 8/8 표기가
// 사실과 달라진다. 승인 필요 항목이 있으면 완료가 아니어야 한다.
test('pending visual approvals keep the profile step out of the completed count', () => {
  for (const pending of [
    { logo_url: null },
    { brand_primary_color: null },
    { site_access_mode: null },
    { hero_headline: null, hero_description: null },
  ]) {
    const steps = deriveOnboardingSteps(
      { ...hospital, ...pending },
      sources,
      philosophies,
      readiness,
      'hospital-id',
      acceptedHandoff,
    )
    const profile = steps.find((step) => step.key === 'profile')
    const onboarding = steps.filter((step) => step.phase === 'onboarding')

    assert.equal(profile?.status, 'current', JSON.stringify(pending))
    assert.equal(onboarding.filter((step) => step.status === 'completed').length, 7)
    assert.equal(onboarding.length, 8)
  }
})

test('the profile step names the remaining visual approvals and clears once approved', () => {
  const blocked = deriveOnboardingSteps(
    { ...hospital, logo_url: null, brand_primary_color: null },
    sources,
    philosophies,
    readiness,
    'hospital-id',
    acceptedHandoff,
  ).find((step) => step.key === 'profile')

  assert.match(blocked?.description ?? '', /시각 승인 2건/)
  assert.match(blocked?.description ?? '', /공식 로고/)
  assert.match(blocked?.description ?? '', /대표색 1개/)

  const approved = deriveOnboardingSteps(hospital, sources, philosophies, readiness, 'hospital-id', acceptedHandoff)
    .find((step) => step.key === 'profile')
  assert.equal(approved?.status, 'completed')
  assert.doesNotMatch(approved?.description ?? '', /승인 필요|시각 승인 \d/)
})

// 사진은 선택 항목이다. 없어도 프로파일 단계는 완료될 수 있어야 한다.
test('missing photos never block the profile step', () => {
  for (const photoCount of [0, null, undefined]) {
    const steps = deriveOnboardingSteps(
      { ...hospital, photo_count: photoCount },
      sources,
      philosophies,
      readiness,
      'hospital-id',
      acceptedHandoff,
    )
    assert.equal(steps.find((step) => step.key === 'profile')?.status, 'completed')
  }
})

// A-7: 3단계 설명은 "초기 진단 + PDF"를 요구한다. 측정만 끝나고 PDF가 없으면
// 완료로 표시하지 않고, 무엇이 빠졌는지 단계 설명이 말한다.
test('an initial diagnosis without a report PDF cannot complete step three', () => {
  const steps = deriveOnboardingSteps(
    hospital,
    sources,
    philosophies,
    { ...readiness, report_count: 1, v0_report_pdf_count: 0 },
    'hospital-id',
    acceptedHandoff,
  )
  const v0 = steps.find((step) => step.key === 'v0')

  assert.equal(v0?.status, 'current')
  assert.match(v0?.description ?? '', /PDF가 아직 없습니다/)
  assert.equal(
    steps.filter((step) => step.phase === 'onboarding' && step.status === 'completed').length,
    7,
  )
})

// 월간 리포트 PDF는 초기 진단이 아니다. 리포트 행이 여러 건이어도 초기 진단 PDF가
// 0이면 3단계는 끝나지 않고, 설명이 "월간 리포트가 아니라"는 점을 짚는다.
test('monthly report PDFs never stand in for the initial diagnosis', () => {
  const v0 = deriveOnboardingSteps(
    hospital,
    sources,
    philosophies,
    { ...readiness, report_count: 4, v0_report_pdf_count: 0 },
    'hospital-id',
    acceptedHandoff,
  ).find((step) => step.key === 'v0')

  assert.equal(v0?.status, 'current')
  assert.match(v0?.description ?? '', /월간 리포트가 아니라 초기 진단 PDF/)
})

test('a report PDF completes step three and an unknown PDF count does not block it', () => {
  for (const readinessVariant of [
    { ...readiness, v0_report_pdf_count: 1 },
    { ...readiness, v0_report_pdf_count: undefined },
  ]) {
    const v0 = deriveOnboardingSteps(
      hospital,
      sources,
      philosophies,
      readinessVariant,
      'hospital-id',
      acceptedHandoff,
    ).find((step) => step.key === 'v0')

    assert.equal(v0?.status, 'completed')
    assert.doesNotMatch(v0?.description ?? '', /아직 없습니다/)
  }
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

test('step 7 carries a badge counting the drafts still waiting for approval', () => {
  const steps = deriveOnboardingSteps(
    hospital,
    sources,
    [
      { status: 'APPROVED', version: 2 },
      { status: 'DRAFT', version: 3 },
      { status: 'DRAFT', version: 4 },
    ],
    readiness,
    'hospital-id',
    acceptedHandoff,
  )
  const step = steps.find((item) => item.key === 'philosophy_approved')

  assert.equal(step?.index, 6)
  assert.equal(step?.badge, '승인 대기 초안 2건')
})

test('drafts older than the approved version are not pending work', () => {
  assert.equal(
    countPendingPhilosophyDrafts([
      { status: 'DRAFT', version: 1 },
      { status: 'DRAFT', version: 2 },
      { status: 'APPROVED', version: 3 },
    ]),
    0,
  )
  assert.equal(
    countPendingPhilosophyDrafts([
      { status: 'DRAFT', version: 1 },
      { status: 'DRAFT', version: 4 },
      { status: 'APPROVED', version: 3 },
    ]),
    1,
  )
})

test('a hospital with no approved version counts every draft as pending', () => {
  assert.equal(
    countPendingPhilosophyDrafts([{ status: 'DRAFT', version: 1 }, { status: 'DRAFT', version: 2 }]),
    2,
  )
})

test('no pending draft means no badge — the step never invents work', () => {
  const steps = deriveOnboardingSteps(
    hospital,
    sources,
    [{ status: 'APPROVED', version: 3 }],
    readiness,
    'hospital-id',
    acceptedHandoff,
  )

  assert.equal(steps.find((item) => item.key === 'philosophy_approved')?.badge, undefined)
  assert.equal(steps.filter((item) => item.badge).length, 0)
})

test('the onboarding sidebar and card both render the step badge', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
    'utf8',
  )

  assert.equal(page.match(/\{step\.badge && \(/g)?.length, 2)
})
