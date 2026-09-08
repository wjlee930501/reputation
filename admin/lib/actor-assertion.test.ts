import assert from 'node:assert/strict'
import test from 'node:test'

import { buildActorAssertion, parseActorAssertionForTest } from './actor-assertion.ts'

test('assertion carries email/role and verifies with the same secret', async () => {
  const token = await buildActorAssertion('s3cret', { email: 'ae@example.com', role: 'OPERATOR' })
  const parsed = await parseActorAssertionForTest('s3cret', token)
  assert.equal(parsed?.email, 'ae@example.com')
  assert.equal(parsed?.role, 'OPERATOR')
  assert.match(token, /^v1\.[A-Za-z0-9_-]+\.[0-9a-f]{64}$/)
})

test('assertion fails with a different secret or after expiry', async () => {
  const expired = await buildActorAssertion(
    's3cret',
    { email: 'ae@example.com', role: 'OPERATOR' },
    { ttlMs: -1 },
  )
  assert.equal(await parseActorAssertionForTest('s3cret', expired), null)

  const fresh = await buildActorAssertion('s3cret', { email: 'ae@example.com', role: 'OPERATOR' })
  assert.equal(await parseActorAssertionForTest('other', fresh), null)
})

test('assertion default lifetime is the 120s replay window the backend enforces', async () => {
  const token = await buildActorAssertion('s3cret', { email: 'ae@example.com', role: 'OWNER' })
  const parsed = await parseActorAssertionForTest('s3cret', token)
  assert.ok(parsed)
  assert.equal(parsed.exp - parsed.iat, 120_000)
  // nonce는 저장되지 않지만(재생 저장소 없음) 같은 값이 반복되면 TTL 창 안에서 구별이 사라진다.
  const other = await parseActorAssertionForTest(
    's3cret',
    await buildActorAssertion('s3cret', { email: 'ae@example.com', role: 'OWNER' }),
  )
  assert.notEqual(parsed.nonce, other?.nonce)
})
