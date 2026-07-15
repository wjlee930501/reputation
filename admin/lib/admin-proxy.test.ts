import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ADMIN_PROXY_TIMEOUT_MS,
  ADMIN_PROXY_SLOW_TIMEOUT_MS,
  adminProxyTimeoutMsForPath,
  buildAdminProxyFetchInit,
  mapAdminProxyFetchError,
} from './admin-proxy.ts'

test('admin proxy fetch init uses no-store and an abort timeout signal', () => {
  const body = new TextEncoder().encode('payload').buffer
  const init = buildAdminProxyFetchInit({
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body,
  })

  assert.equal(init.method, 'PATCH')
  assert.deepEqual(init.headers, { 'content-type': 'application/json' })
  assert.equal(init.body, body)
  assert.equal(init.cache, 'no-store')
  assert.ok(init.signal instanceof AbortSignal)
  assert.equal(ADMIN_PROXY_TIMEOUT_MS <= 15_000, true)
})

test('LLM-backed autofill, source processing, and philosophy drafting receive the slow timeout', () => {
  assert.equal(ADMIN_PROXY_SLOW_TIMEOUT_MS >= 210_000, true)
  for (const path of [
    'hospitals/demo/profile/autofill',
    'hospitals/demo/essence/sources/source-id/process',
    'hospitals/demo/essence/philosophy/draft',
  ]) {
    assert.equal(adminProxyTimeoutMsForPath(path), ADMIN_PROXY_SLOW_TIMEOUT_MS, path)
  }
})

test('nearby reads and ordinary writes keep the short timeout', () => {
  for (const path of [
    'hospitals/demo/profile',
    'hospitals/demo/essence/sources/source-id',
    'hospitals/demo/essence/philosophies',
    'hospitals/demo/essence/philosophy/draft/extra',
  ]) {
    assert.equal(adminProxyTimeoutMsForPath(path), ADMIN_PROXY_TIMEOUT_MS, path)
  }
})

test('admin proxy maps timeout and network errors to normalized statuses', () => {
  assert.deepEqual(mapAdminProxyFetchError(new DOMException('deadline', 'TimeoutError')), {
    status: 504,
    error: 'Admin service timed out',
  })
  assert.deepEqual(mapAdminProxyFetchError(new Error('socket closed')), {
    status: 502,
    error: 'Admin service unavailable',
  })
})
