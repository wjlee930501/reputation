import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ADMIN_SESSION_REVOCATION_TIMEOUT_MS,
  checkAdminSessionRevocation,
  revokeAdminSession,
} from './session-revocation.ts'

const sessionToken = 'signed-session-token'

test('admin revocation checks use no-store fetches with a bounded timeout', async () => {
  const originalFetch = globalThis.fetch
  let requestInit: RequestInit | undefined

  const fetchMock: typeof fetch = async (_input, init) => {
    requestInit = init
    return new Response(JSON.stringify({ revoked: false }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const status = await checkAdminSessionRevocation({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken,
    })

    assert.equal(status, 'active')
    assert.equal(requestInit?.cache, 'no-store')
    assert.equal(new Headers(requestInit?.headers).get('X-Admin-Key'), 'test-admin-key')
    const signal = requestInit?.signal
    assert.ok(signal instanceof AbortSignal)
    assert.equal(signal.aborted, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('admin session revocation writes also use a bounded timeout', async () => {
  const originalFetch = globalThis.fetch
  let requestInit: RequestInit | undefined

  const fetchMock: typeof fetch = async (_input, init) => {
    requestInit = init
    return new Response(JSON.stringify({ revoked: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const status = await revokeAdminSession({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken,
      expiresAtMs: Date.now() + ADMIN_SESSION_REVOCATION_TIMEOUT_MS,
    })

    assert.equal(status, 'revoked')
    assert.equal(requestInit?.cache, 'no-store')
    assert.equal(requestInit?.method, 'POST')
    const signal = requestInit?.signal
    assert.ok(signal instanceof AbortSignal)
    assert.equal(signal.aborted, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('admin revocation checks fail closed when the bounded request is aborted', async () => {
  const originalFetch = globalThis.fetch

  const fetchMock: typeof fetch = async () => {
    throw new DOMException('deadline', 'TimeoutError')
  }
  globalThis.fetch = fetchMock

  try {
    const status = await checkAdminSessionRevocation({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken,
    })

    assert.equal(status, 'unavailable')
  } finally {
    globalThis.fetch = originalFetch
  }
})
