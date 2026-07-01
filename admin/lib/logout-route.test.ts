import assert from 'node:assert/strict'
import test from 'node:test'

import { NextRequest } from 'next/server.js'

import { POST } from '../app/api/auth/logout/route.ts'
import { generateSessionToken } from './session.ts'

const csrfToken = 'csrf-token-from-login'
const sessionPayload = {
  accountId: '0f0a41a9-bf2c-4f7b-b182-b85dc729b6e4',
  email: 'owner@example.com',
  name: 'Owner',
  role: 'OWNER',
  csrfToken,
}

async function buildLogoutRequest(sessionToken: string, csrfHeader?: string): Promise<NextRequest> {
  return new NextRequest('https://admin.example.test/api/auth/logout', {
    method: 'POST',
    headers: {
      cookie: `admin_session=${sessionToken}`,
      host: 'admin.example.test',
      origin: 'https://admin.example.test',
      ...(csrfHeader ? { 'x-admin-csrf-token': csrfHeader } : {}),
    },
  })
}

test('logout revokes the session hash before clearing admin cookies', async () => {
  const originalFetch = globalThis.fetch
  process.env.ADMIN_SECRET_KEY = 'test-admin-key'
  process.env.ADMIN_SESSION_SECRET = 'test-session-secret'
  process.env.BACKEND_URL = 'https://backend.example.test'
  const sessionToken = await generateSessionToken('test-session-secret', 60, sessionPayload)
  let revokePayload: unknown

  const fetchMock: typeof fetch = async (input, init) => {
    assert.equal(String(input), 'https://backend.example.test/api/v1/admin/auth/sessions/revoke')
    assert.equal(init?.method, 'POST')
    assert.equal(new Headers(init?.headers).get('X-Admin-Key'), 'test-admin-key')
    revokePayload = JSON.parse(String(init?.body))
    return new Response(JSON.stringify({ revoked: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const res = await POST(await buildLogoutRequest(sessionToken, csrfToken))
    const body = await res.json()

    assert.deepEqual(body, { ok: true })
    assert.equal(res.status, 200)
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.match(res.headers.get('set-cookie') ?? '', /admin_session=;/)
    assert.match(res.headers.get('set-cookie') ?? '', /admin_csrf=;/)
    assert.ok(
      typeof revokePayload === 'object' &&
        revokePayload !== null &&
        'token_hash' in revokePayload &&
        typeof revokePayload.token_hash === 'string' &&
        /^[0-9a-f]{64}$/.test(revokePayload.token_hash) &&
        revokePayload.token_hash !== sessionToken,
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('logout rejects a valid session without the CSRF header and does not revoke it', async () => {
  const originalFetch = globalThis.fetch
  process.env.ADMIN_SESSION_SECRET = 'test-session-secret'
  const sessionToken = await generateSessionToken('test-session-secret', 60, sessionPayload)
  let revokeCalled = false

  const fetchMock: typeof fetch = async () => {
    revokeCalled = true
    return new Response(JSON.stringify({ revoked: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const res = await POST(await buildLogoutRequest(sessionToken))

    assert.equal(res.status, 403)
    assert.equal(await res.text(), 'Forbidden')
    assert.equal(revokeCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('logout allows a legacy pre-CSRF session to clear cookies', async () => {
  const originalFetch = globalThis.fetch
  process.env.ADMIN_SECRET_KEY = 'test-admin-key'
  process.env.ADMIN_SESSION_SECRET = 'test-session-secret'
  process.env.BACKEND_URL = 'https://backend.example.test'
  const legacyPayload = {
    accountId: sessionPayload.accountId,
    email: sessionPayload.email,
    name: sessionPayload.name,
    role: sessionPayload.role,
  }
  const sessionToken = await generateSessionToken('test-session-secret', 60, legacyPayload)
  let revokeCalled = false

  const fetchMock: typeof fetch = async (input) => {
    assert.equal(String(input), 'https://backend.example.test/api/v1/admin/auth/sessions/revoke')
    revokeCalled = true
    return new Response(JSON.stringify({ revoked: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const res = await POST(await buildLogoutRequest(sessionToken))
    const body = await res.json()

    assert.equal(res.status, 200)
    assert.deepEqual(body, { ok: true })
    assert.equal(revokeCalled, true)
    assert.match(res.headers.get('set-cookie') ?? '', /admin_session=;/)
    assert.match(res.headers.get('set-cookie') ?? '', /admin_csrf=;/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('logout fails closed when the backend does not confirm revocation', async () => {
  const originalFetch = globalThis.fetch
  process.env.ADMIN_SECRET_KEY = 'test-admin-key'
  process.env.ADMIN_SESSION_SECRET = 'test-session-secret'
  process.env.BACKEND_URL = 'https://backend.example.test'
  const sessionToken = await generateSessionToken('test-session-secret', 60, sessionPayload)

  const fetchMock: typeof fetch = async () => {
    return new Response(JSON.stringify({ revoked: false }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const res = await POST(await buildLogoutRequest(sessionToken, csrfToken))

    assert.equal(res.status, 503)
    assert.equal(await res.text(), 'Admin session state unavailable')
    assert.equal(res.headers.get('set-cookie'), null)
  } finally {
    globalThis.fetch = originalFetch
  }
})
