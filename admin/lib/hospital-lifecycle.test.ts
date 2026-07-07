import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getHospitalLifecycleAction,
  hospitalLifecycleActionPath,
  hospitalLifecycleConfirmMessage,
} from './hospital-lifecycle.ts'

test('getHospitalLifecycleAction offers pause for ACTIVE and PENDING_DOMAIN', () => {
  assert.equal(getHospitalLifecycleAction('ACTIVE'), 'pause')
  assert.equal(getHospitalLifecycleAction('PENDING_DOMAIN'), 'pause')
})

test('getHospitalLifecycleAction offers resume only for PAUSED', () => {
  assert.equal(getHospitalLifecycleAction('PAUSED'), 'resume')
})

test('getHospitalLifecycleAction has no action for onboarding/build states or missing status', () => {
  assert.equal(getHospitalLifecycleAction('ONBOARDING'), null)
  assert.equal(getHospitalLifecycleAction('ANALYZING'), null)
  assert.equal(getHospitalLifecycleAction('BUILDING'), null)
  assert.equal(getHospitalLifecycleAction(null), null)
  assert.equal(getHospitalLifecycleAction(undefined), null)
})

test('hospitalLifecycleConfirmMessage warns that automation stops before pausing', () => {
  assert.match(hospitalLifecycleConfirmMessage('pause'), /콘텐츠 자동 생성·측정이 중단/)
  assert.match(hospitalLifecycleConfirmMessage('resume'), /다시 시작/)
})

test('hospitalLifecycleActionPath builds the admin proxy path for pause/resume', () => {
  assert.equal(hospitalLifecycleActionPath('h-1', 'pause'), '/admin/hospitals/h-1/pause')
  assert.equal(hospitalLifecycleActionPath('h-1', 'resume'), '/admin/hospitals/h-1/resume')
})
