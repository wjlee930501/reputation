import assert from 'node:assert/strict'
import test from 'node:test'

import {
  LOGIN_PROXY_TIMEOUT_MS,
  buildLoginFetchInit,
  mapLoginFetchError,
} from './login-proxy.ts'

test('login proxy fetch init uses no-store and an abort timeout signal', () => {
  const init = buildLoginFetchInit({
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: 'owner@example.com', password: 'bad' }),
  })

  assert.equal(init.cache, 'no-store')
  assert.equal(init.method, 'POST')
  assert.ok(init.signal instanceof AbortSignal)
  assert.equal(LOGIN_PROXY_TIMEOUT_MS <= 10_000, true)
})

test('login proxy maps timeout and network errors to normalized statuses', () => {
  assert.deepEqual(mapLoginFetchError(new DOMException('deadline', 'TimeoutError')), {
    status: 504,
    error: 'Authentication service timed out',
  })
  assert.deepEqual(mapLoginFetchError(new Error('socket closed')), {
    status: 502,
    error: 'Authentication service unavailable',
  })
})
