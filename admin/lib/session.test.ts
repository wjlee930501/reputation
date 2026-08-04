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
  assert.equal(typeof session?.issuedAt, 'number')
  assert.deepEqual(session, {
    ...sessionPayload,
    issuedAt: session?.issuedAt,
    expiresAt: session?.expiresAt,
  })
})

test('session tokens carry a signed issue time so accounts can revoke past sessions', async () => {
  // 백엔드가 계정의 sessions_invalid_before와 비교하는 값이다. 서명 밖에 있으면
  // 위조해 비밀번호 재설정 후에도 옛 세션을 살릴 수 있으므로 payload 안에 있어야 한다.
  const before = Date.now()
  const token = await generateSessionToken('test-secret', 60, sessionPayload)
  const after = Date.now()

  const session = await readSessionToken(token, 'test-secret')
  assert.ok(session?.issuedAt !== undefined)
  assert.ok(session.issuedAt >= before && session.issuedAt <= after)

  // 발급 시각을 바꾸면 서명이 깨져 토큰 자체가 거부된다.
  const [nonce, expiresAt, payloadHex, signature] = token.split('.')
  const tampered = JSON.parse(Buffer.from(payloadHex, 'hex').toString())
  tampered.issuedAt = session.issuedAt + 60_000
  const forgedPayload = Buffer.from(JSON.stringify(tampered)).toString('hex')

  assert.equal(
    await readSessionToken(`${nonce}.${expiresAt}.${forgedPayload}.${signature}`, 'test-secret'),
    null,
  )
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
