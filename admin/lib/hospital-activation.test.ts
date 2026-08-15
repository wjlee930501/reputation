import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isPlatformAddressBrowsable,
  missingActivationPrerequisites,
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
