import assert from 'node:assert/strict'
import test from 'node:test'

import {
  hasCustomDomain,
  isPlatformAddressBrowsable,
  missingActivationPrerequisites,
  platformActivationMode,
  readServerActivationBlockers,
} from './hospital-activation.ts'

test('client preview follows the STEP 5 activation gate', () => {
  const ready = {
    profile_complete: true,
    v0_report_done: true,
    site_built: true,
  }
  assert.deepEqual(missingActivationPrerequisites(ready), [])
})

test('site_built preview never makes the public platform address browsable before LIVE', () => {
  assert.equal(isPlatformAddressBrowsable({ site_live: false }), false)
  assert.equal(isPlatformAddressBrowsable({ site_live: true }), true)
})

test('platform activation preview does not require content scheduling', () => {
  const missing = missingActivationPrerequisites({ profile_complete: true, v0_report_done: true, site_built: true })
  assert.deepEqual(missing, [])
})

test('server-provided activation blockers remain authoritative and canonically ordered', () => {
  const blockers = readServerActivationBlockers({
    code: 'ACTIVATION_PREREQUISITES_MISSING',
    missing: ['site_built', 'profile_complete'],
    prerequisites: [
      { key: 'site_built', label: '서버 허브 차단', action: '서버 허브 액션', passed: false },
      { key: 'profile_complete', label: '서버 프로필 차단', action: '서버 프로필 액션', passed: false },
    ],
  })

  assert.deepEqual(blockers, [
    { key: 'profile_complete', label: '서버 프로필 차단', action: '서버 프로필 액션' },
    { key: 'site_built', label: '서버 허브 차단', action: '서버 허브 액션' },
  ])
})

test('platform address activates automatically once the three gates pass', () => {
  const gatesMet = { profile_complete: true, v0_report_done: true, site_built: true }
  assert.equal(platformActivationMode({ ...gatesMet, site_live: false }), 'automatic')
  assert.equal(platformActivationMode({ ...gatesMet, site_live: true }), 'live')
})

test('custom-domain and paused hospitals keep the manual activation path', () => {
  const gatesMet = { profile_complete: true, v0_report_done: true, site_built: true }
  assert.equal(
    platformActivationMode({ ...gatesMet, site_live: false, aeo_domain: 'ai.clinic.co.kr' }),
    'manual',
  )
  assert.equal(platformActivationMode({ ...gatesMet, site_live: false, status: 'PAUSED' }), 'manual')
  assert.equal(hasCustomDomain({ aeo_domain: '   ' }), false)
  assert.equal(hasCustomDomain({ aeo_domain: 'ai.clinic.co.kr' }), true)
})

test('unmet gates never read as automatic activation', () => {
  assert.equal(
    platformActivationMode({ profile_complete: true, v0_report_done: false, site_built: true }),
    'blocked',
  )
})

test('platformActivationMode matches backend evaluate_auto_activation ordering (custom domain / PAUSED before gate check)', () => {
  // 백엔드 evaluate_auto_activation: ACTIVE → status not auto-advanceable(PAUSED) →
  // custom domain pending → gates not met. 자기 도메인 지정 병원은 게이트가 아직
  // 안 통과했어도 CUSTOM_DOMAIN_PENDING을 먼저 반환하므로 화면도 'manual'이어야 한다.
  assert.equal(
    platformActivationMode({
      profile_complete: true,
      v0_report_done: false,
      site_built: true,
      site_live: false,
      aeo_domain: 'ai.clinic.co.kr',
    }),
    'manual',
  )

  // PAUSED 병원도 게이트 미충족과 무관하게 STATUS_NOT_AUTO_ADVANCEABLE이 먼저 걸려 'manual'.
  assert.equal(
    platformActivationMode({
      profile_complete: true,
      v0_report_done: false,
      site_built: true,
      site_live: false,
      status: 'PAUSED',
    }),
    'manual',
  )

  // 자기 도메인도 없고 PAUSED도 아닌데 게이트만 미충족이면 'blocked' (자동 활성화 안내 문구 대상).
  assert.equal(
    platformActivationMode({
      profile_complete: true,
      v0_report_done: false,
      site_built: true,
      site_live: false,
    }),
    'blocked',
  )
})
