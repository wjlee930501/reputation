import assert from 'node:assert/strict'
import test from 'node:test'

import { isPubliclyServing, publicServiceState } from './public-service-state.ts'

test('ACTIVE with site_live is the only live state', () => {
  const verdict = { kind: 'live' as const, remaining: [] }
  assert.equal(publicServiceState(verdict), 'live')
  assert.equal(isPubliclyServing(verdict), true)
})

test('a paused hospital is paused even though pause leaves site_live true', () => {
  const verdict = { kind: 'paused' as const, remaining: [] }
  assert.equal(publicServiceState(verdict), 'paused')
  assert.equal(isPubliclyServing(verdict), false)
})

test('the client consumes backend blockers without recomputing flags', () => {
  const verdict = { kind: 'not_live' as const, remaining: ['future_backend_blocker'] }
  assert.equal(publicServiceState(verdict), 'not_live')
  assert.deepEqual(verdict.remaining, ['future_backend_blocker'])
  assert.equal(publicServiceState(null), 'not_live')
  assert.equal(publicServiceState(undefined), 'not_live')
})
