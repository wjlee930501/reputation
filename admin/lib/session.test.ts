import assert from 'node:assert/strict'
import test from 'node:test'

import {
  generateCsrfToken,
  generateSessionToken,
  hashSessionToken,
  readSessionToken,
  verifySessionToken,
} from './session.ts'

const sessionPayload = {
  accountId: '0f0a41a9-bf2c-4f7b-b182-b85dc729b6e4',
  email: 'owner@example.com',
  name: 'Owner',
  role: 'OWNER',
  csrfToken: 'csrf-token-from-login',
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

test('generated CSRF tokens are opaque hex nonces for admin write requests', () => {
  const token = generateCsrfToken()

  assert.match(token, /^[0-9a-f]{64}$/)
})

test('session token hashes are deterministic non-secret revocation keys', async () => {
  const first = await hashSessionToken('admin-session-token-a')
  const same = await hashSessionToken('admin-session-token-a')
  const different = await hashSessionToken('admin-session-token-b')

  assert.match(first, /^[0-9a-f]{64}$/)
  assert.equal(first, same)
  assert.notEqual(first, different)
})
