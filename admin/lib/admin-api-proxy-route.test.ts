import assert from 'node:assert/strict'
import test from 'node:test'

import { NextRequest } from 'next/server.js'

import { handleAdminApiProxy } from './admin-api-proxy-route.ts'
import { generateSessionToken } from './session.ts'

const sessionPayload = {
  accountId: '0f0a41a9-bf2c-4f7b-b182-b85dc729b6e4',
  email: 'owner@example.com',
  name: 'Owner',
  role: 'OWNER',
}

async function buildAuthorizedRequest(method: string): Promise<NextRequest> {
  const secret = 'test-session-secret'
  const token = await generateSessionToken(secret, 60, sessionPayload)
  process.env.ADMIN_SECRET_KEY = 'test-admin-key'
  process.env.ADMIN_SESSION_SECRET = secret
  process.env.BACKEND_URL = 'https://backend.example.test'

  return new NextRequest('https://admin.example.test/api/admin/hospitals?limit=1', {
    method,
    headers: {
      cookie: `admin_session=${token}`,
      host: 'admin.example.test',
      origin: 'https://admin.example.test',
      'content-type': 'application/json',
    },
    body: method === 'GET' ? undefined : JSON.stringify({ name: 'demo' }),
  })
}

async function withFetchThrowing(error: unknown, callback: () => Promise<void>) {
  const originalFetch = globalThis.fetch
  globalThis.fetch = (async () => {
    throw error
  }) as typeof fetch
  try {
    await callback()
  } finally {
    globalThis.fetch = originalFetch
  }
}

test('admin API route returns no-store 504 when upstream fetch times out', async () => {
  await withFetchThrowing(new DOMException('deadline', 'TimeoutError'), async () => {
    const res = await handleAdminApiProxy(await buildAuthorizedRequest('POST'), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(res.status, 504)
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.deepEqual(await res.json(), { error: 'Admin service timed out' })
  })
})

test('admin API route returns no-store 502 when upstream fetch fails', async () => {
  await withFetchThrowing(new Error('socket closed'), async () => {
    const res = await handleAdminApiProxy(await buildAuthorizedRequest('GET'), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(res.status, 502)
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.deepEqual(await res.json(), { error: 'Admin service unavailable' })
  })
})
