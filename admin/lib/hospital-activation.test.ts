import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canActivateHospital,
  isPlatformAddressBrowsable,
  missingActivationPrerequisites,
} from './hospital-activation.ts'

test('platform activation is allowed only after every backend prerequisite', () => {
  const ready = {
    profile_complete: true,
    v0_report_done: true,
    site_built: true,
  }
  assert.equal(canActivateHospital(ready), true)
  assert.deepEqual(missingActivationPrerequisites(ready), [])
})

test('site_built preview never makes the public platform address browsable before LIVE', () => {
  assert.equal(isPlatformAddressBrowsable({ site_live: false }), false)
  assert.equal(isPlatformAddressBrowsable({ site_live: true }), true)
})

test('platform activation follows the official pre-LIVE gates and ignores later schedule work', () => {
  const missing = missingActivationPrerequisites({ profile_complete: true, site_built: true })
  assert.deepEqual(
    missing.map(({ key, label, hrefSuffix }) => ({ key, label, hrefSuffix })),
    [
      { key: 'v0_report_done', label: 'V0 리포트 생성', hrefSuffix: 'reports' },
    ],
  )
})
