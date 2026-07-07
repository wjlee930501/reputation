import assert from 'node:assert/strict'
import test from 'node:test'

import { NextRequest } from 'next/server.js'

import { handleAdminApiProxy } from './admin-api-proxy-route.ts'
import { clearAdminSessionRevocationCache } from './session-revocation.ts'
import { generateSessionToken } from './session.ts'

const sessionPayload = {
  accountId: '0f0a41a9-bf2c-4f7b-b182-b85dc729b6e4',
  email: 'owner@example.com',
  name: 'Owner',
  role: 'OWNER',
  csrfToken: 'csrf-token-from-login',
}

async function buildAuthorizedRequest(
  method: string,
  csrfToken?: string,
  tokenOverride?: string,
): Promise<NextRequest> {
  const secret = 'test-session-secret'
  const token = tokenOverride ?? (await generateSessionToken(secret, 60, sessionPayload))
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
      ...(csrfToken ? { 'x-admin-csrf-token': csrfToken } : {}),
    },
    body: method === 'GET' ? undefined : JSON.stringify({ name: 'demo' }),
  })
}

async function withActiveRevocationThenThrowing(error: unknown, callback: () => Promise<void>) {
  const originalFetch = globalThis.fetch
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.includes('/api/v1/admin/auth/sessions/') && url.endsWith('/revocation')) {
      return new Response(JSON.stringify({ revoked: false }), { status: 200 })
    }
    throw error
  }
  globalThis.fetch = fetchMock
  try {
    await callback()
  } finally {
    globalThis.fetch = originalFetch
  }
}

test('admin API route returns no-store 504 when upstream fetch times out', async () => {
  await withActiveRevocationThenThrowing(new DOMException('deadline', 'TimeoutError'), async () => {
    const res = await handleAdminApiProxy(await buildAuthorizedRequest('POST', sessionPayload.csrfToken), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(res.status, 504)
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.deepEqual(await res.json(), { error: 'Admin service timed out' })
  })
})

test('admin API route rejects a backend-revoked session before proxying', async () => {
  const originalFetch = globalThis.fetch
  let proxied = false
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.includes('/api/v1/admin/auth/sessions/') && url.endsWith('/revocation')) {
      return new Response(JSON.stringify({ revoked: true }), { status: 200 })
    }
    proxied = true
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const res = await handleAdminApiProxy(await buildAuthorizedRequest('GET'), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(res.status, 401)
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.deepEqual(await res.json(), { error: 'Unauthorized' })
    assert.equal(proxied, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('admin API route rejects invalid proxy paths before revocation lookup', async () => {
  const originalFetch = globalThis.fetch
  let fetchCalled = false
  const fetchMock: typeof fetch = async () => {
    fetchCalled = true
    return new Response(JSON.stringify({ revoked: false }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const res = await handleAdminApiProxy(await buildAuthorizedRequest('GET'), {
      params: Promise.resolve({ path: ['..', 'hospitals'] }),
    })

    assert.equal(res.status, 403)
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.equal(await res.text(), 'Forbidden')
    assert.equal(fetchCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('admin API route rejects state-changing requests without or with mismatched CSRF nonce', async () => {
  for (const method of ['POST', 'PATCH', 'DELETE']) {
    const missing = await handleAdminApiProxy(await buildAuthorizedRequest(method), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(missing.status, 403)
    assert.equal(missing.headers.get('cache-control'), 'no-store, private')
    assert.equal(await missing.text(), 'Forbidden')

    const mismatched = await handleAdminApiProxy(await buildAuthorizedRequest(method, 'wrong-csrf'), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(mismatched.status, 403)
    assert.equal(mismatched.headers.get('cache-control'), 'no-store, private')
    assert.equal(await mismatched.text(), 'Forbidden')
  }
})

test('admin API route returns no-store 502 when upstream fetch fails', async () => {
  await withActiveRevocationThenThrowing(new Error('socket closed'), async () => {
    const res = await handleAdminApiProxy(await buildAuthorizedRequest('GET'), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(res.status, 502)
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.deepEqual(await res.json(), { error: 'Admin service unavailable' })
  })
})

test('admin API route bypasses the revocation cache for state-changing requests', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch
  let revocationCalls = 0
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.includes('/api/v1/admin/auth/sessions/') && url.endsWith('/revocation')) {
      revocationCalls += 1
      return new Response(JSON.stringify({ revoked: false }), { status: 200 })
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const secret = 'test-session-secret'
    const token = await generateSessionToken(secret, 60, sessionPayload)

    // GET으로 'active'를 캐시에 채운다.
    const warm = await handleAdminApiProxy(await buildAuthorizedRequest('GET', undefined, token), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })
    assert.equal(warm.status, 200)
    assert.equal(revocationCalls, 1)

    // 이어지는 상태 변경 요청(POST)은 캐시된 'active'를 신뢰하지 않고 백엔드로 재확인한다.
    const write = await handleAdminApiProxy(
      await buildAuthorizedRequest('POST', sessionPayload.csrfToken, token),
      { params: Promise.resolve({ path: ['hospitals'] }) },
    )
    assert.equal(write.status, 200)
    assert.equal(revocationCalls, 2, '상태 변경 요청은 캐시를 우회해 매번 백엔드로 폐기 여부를 재확인해야 한다')
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})

test('admin API route reuses a cached "active" revocation check for repeated requests', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch
  let revocationCalls = 0
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.includes('/api/v1/admin/auth/sessions/') && url.endsWith('/revocation')) {
      revocationCalls += 1
      return new Response(JSON.stringify({ revoked: false }), { status: 200 })
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const secret = 'test-session-secret'
    const token = await generateSessionToken(secret, 60, sessionPayload)

    const first = await handleAdminApiProxy(await buildAuthorizedRequest('GET', undefined, token), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })
    const second = await handleAdminApiProxy(await buildAuthorizedRequest('GET', undefined, token), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    assert.equal(first.status, 200)
    assert.equal(second.status, 200)
    assert.equal(revocationCalls, 1, '같은 세션의 두 번째 요청은 캐시된 폐기 확인 결과를 재사용해야 한다')
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})
