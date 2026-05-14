import assert from 'node:assert/strict'
import test from 'node:test'

import { generateSessionToken, verifySessionToken } from './session.ts'

test('session tokens expire server-side', async () => {
  const token = await generateSessionToken('test-secret', -1)

  assert.equal(await verifySessionToken(token, 'test-secret'), false)
})

test('session tokens verify before expiry', async () => {
  const token = await generateSessionToken('test-secret', 60)

  assert.equal(await verifySessionToken(token, 'test-secret'), true)
  assert.equal(await verifySessionToken(token, 'wrong-secret'), false)
})
