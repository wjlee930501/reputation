import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isPlatformAddressBrowsable,
  missingActivationPrerequisites,
  readServerActivationBlockers,
} from './hospital-activation.ts'

test('client preview includes schedule but never authorizes activation', () => {
  const ready = {
    profile_complete: true,
    v0_report_done: true,
    site_built: true,
    schedule_set: true,
  }
  assert.deepEqual(missingActivationPrerequisites(ready), [])
})

test('site_built preview never makes the public platform address browsable before LIVE', () => {
  assert.equal(isPlatformAddressBrowsable({ site_live: false }), false)
  assert.equal(isPlatformAddressBrowsable({ site_live: true }), true)
})

test('platform activation preview keeps schedule before the domain action', () => {
  const missing = missingActivationPrerequisites({ profile_complete: true, v0_report_done: true, site_built: true })
  assert.deepEqual(
    missing.map(({ key, label, hrefSuffix }) => ({ key, label, hrefSuffix })),
    [{ key: 'schedule_set', label: '콘텐츠 스케줄 설정', hrefSuffix: 'schedule' }],
  )
})

test('server-provided activation blockers remain authoritative and canonically ordered', () => {
  const blockers = readServerActivationBlockers({
    code: 'ACTIVATION_PREREQUISITES_MISSING',
    missing: ['schedule_set', 'handoff_accepted'],
    prerequisites: [
      { key: 'schedule_set', label: '서버 스케줄 차단', action: '서버 스케줄 액션', passed: false },
      { key: 'site_built', label: '서버 허브', action: '서버 허브 액션', passed: true },
      { key: 'handoff_accepted', label: '서버 인수 차단', action: '서버 인수 액션', passed: false },
    ],
  })

  assert.deepEqual(blockers, [
    { key: 'handoff_accepted', label: '서버 인수 차단', action: '서버 인수 액션' },
    { key: 'schedule_set', label: '서버 스케줄 차단', action: '서버 스케줄 액션' },
  ])
})
