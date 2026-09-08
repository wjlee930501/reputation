import assert from 'node:assert/strict'
import test from 'node:test'

import { isPubliclyServing, publicServiceState } from './public-service-state.ts'

test('ACTIVE with site_live is the only live state', () => {
  assert.equal(publicServiceState({ status: 'ACTIVE', site_live: true }), 'live')
  assert.equal(isPubliclyServing({ status: 'ACTIVE', site_live: true }), true)
})

test('a paused hospital is paused even though pause leaves site_live true', () => {
  assert.equal(publicServiceState({ status: 'PAUSED', site_live: true }), 'paused')
  assert.equal(isPubliclyServing({ status: 'PAUSED', site_live: true }), false)
})

test('every other combination is not live', () => {
  assert.equal(publicServiceState({ status: 'ACTIVE', site_live: false }), 'not_live')
  assert.equal(publicServiceState({ status: 'PENDING_DOMAIN', site_live: true }), 'not_live')
  assert.equal(publicServiceState({ status: 'ONBOARDING', site_live: false }), 'not_live')
  assert.equal(publicServiceState({ site_live: true }), 'not_live')
  assert.equal(publicServiceState(null), 'not_live')
  assert.equal(publicServiceState(undefined), 'not_live')
})
