import assert from 'node:assert/strict'
import test from 'node:test'

import { generateSessionToken, readSessionToken, verifySessionToken } from './session.ts'

const sessionPayload = {
  accountId: '0f0a41a9-bf2c-4f7b-b182-b85dc729b6e4',
  email: 'owner@example.com',
  name: 'Owner',
  role: 'OWNER',
}

test('session tokens expire server-side', async () => {
  const token = await generateSessionToken('test-secret', -1, sessionPayload)

  assert.equal(await verifySessionToken(token, 'test-secret'), false)
  assert.equal(await readSessionToken(token, 'test-secret'), null)
})

test('session tokens verify before expiry', async () => {
  const token = await generateSessionToken('test-secret', 60, sessionPayload)

  assert.equal(await verifySessionToken(token, 'test-secret'), true)
  assert.equal(await verifySessionToken(token, 'wrong-secret'), false)

  const session = await readSessionToken(token, 'test-secret')
  assert.equal(typeof session?.expiresAt, 'number')
  assert.deepEqual(session, {
    ...sessionPayload,
    expiresAt: session?.expiresAt,
  })
})
