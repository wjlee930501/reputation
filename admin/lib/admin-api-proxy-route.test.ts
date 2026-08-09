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
    if (url.includes('/api/v1/admin/auth/sessions/') && url.includes('/revocation')) {
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
    if (url.includes('/api/v1/admin/auth/sessions/') && url.includes('/revocation')) {
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
    if (url.includes('/api/v1/admin/auth/sessions/') && url.includes('/revocation')) {
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
    if (url.includes('/api/v1/admin/auth/sessions/') && url.includes('/revocation')) {
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

test('admin API route proxies the global control-plane prefixes', async () => {
  // 백엔드에 /admin/operations(비용 가드)·/admin/accounts(운영자 계정) 라우터가 있어도
  // 이 허용 목록에 없으면 프록시가 403으로 끊는다 — 화면을 만들어도 동작하지 않는다.
  const originalFetch = globalThis.fetch
  const proxiedPaths: string[] = []
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.includes('/api/v1/admin/auth/sessions/') && url.includes('/revocation')) {
      return new Response(JSON.stringify({ revoked: false }), { status: 200 })
    }
    proxiedPaths.push(new URL(url).pathname)
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    for (const path of [['operations', 'cost-guard'], ['accounts']]) {
      const res = await handleAdminApiProxy(await buildAuthorizedRequest('GET'), {
        params: Promise.resolve({ path }),
      })
      assert.equal(res.status, 200, `/${path.join('/')} must be proxied, not blocked`)
    }

    assert.deepEqual(proxiedPaths, [
      '/api/v1/admin/operations/cost-guard',
      '/api/v1/admin/accounts',
    ])
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})

test('admin API route proxies handoff list and transition routes', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch
  const proxied: Array<{ method: string; path: string }> = []
  const fetchMock: typeof fetch = async (input, init) => {
    const url = String(input)
    if (url.includes('/api/v1/admin/auth/sessions/') && url.includes('/revocation')) {
      return new Response(JSON.stringify({ revoked: false }), { status: 200 })
    }
    proxied.push({ method: init?.method ?? 'GET', path: new URL(url).pathname })
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const handoffId = 'ea0f6a57-d8d7-4447-9f89-baf1b329f2dd'
    const routes = [
      { method: 'GET', path: ['handoffs'] },
      { method: 'POST', path: ['handoffs', handoffId, 'contract'] },
      { method: 'POST', path: ['handoffs', handoffId, 'accept'] },
    ]
    for (const route of routes) {
      const request = await buildAuthorizedRequest(
        route.method,
        route.method === 'POST' ? sessionPayload.csrfToken : undefined,
      )
      const res = await handleAdminApiProxy(request, {
        params: Promise.resolve({ path: route.path }),
      })
      assert.equal(res.status, 200, `${route.method} /${route.path.join('/')} must reach the backend`)
    }

    assert.deepEqual(proxied, [
      { method: 'GET', path: '/api/v1/admin/handoffs' },
      { method: 'POST', path: `/api/v1/admin/handoffs/${handoffId}/contract` },
      { method: 'POST', path: `/api/v1/admin/handoffs/${handoffId}/accept` },
    ])
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})

test('admin API route rejects a handoffs sibling prefix and traversal before backend fetch', async () => {
  const originalFetch = globalThis.fetch
  let fetchCalled = false
  globalThis.fetch = async () => {
    fetchCalled = true
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }

  try {
    for (const path of [['handoffs-admin'], ['handoffs', '..', 'accounts']]) {
      const res = await handleAdminApiProxy(await buildAuthorizedRequest('GET'), {
        params: Promise.resolve({ path }),
      })
      assert.equal(res.status, 403)
      assert.equal(await res.text(), 'Forbidden')
    }
    assert.equal(fetchCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('admin API route asks the backend to judge the session account, not just the token hash', async () => {
  // 계정을 정지해도 이미 발급된 세션 쿠키는 만료(최대 7일)까지 살아 있다. 폐기 조회에
  // account_id를 함께 보내야 백엔드가 계정 상태까지 보고 즉시 끊을 수 있다.
  const originalFetch = globalThis.fetch
  let revocationUrl = ''
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.includes('/api/v1/admin/auth/sessions/') && url.includes('/revocation')) {
      revocationUrl = url
      return new Response(JSON.stringify({ revoked: false }), { status: 200 })
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    await handleAdminApiProxy(await buildAuthorizedRequest('POST', sessionPayload.csrfToken), {
      params: Promise.resolve({ path: ['hospitals'] }),
    })

    const params = new URL(revocationUrl).searchParams
    assert.equal(params.get('account_id'), sessionPayload.accountId)
    // 발급 시각까지 보내야 "비밀번호 재설정 이전 세션만" 끊을 수 있다 —
    // 없으면 백엔드가 전부 무효로 볼 수밖에 없다.
    const issuedAt = params.get('issued_at')
    assert.ok(issuedAt, 'issued_at must be sent')
    assert.ok(Number.isFinite(Date.parse(issuedAt)), 'issued_at must be an ISO timestamp')
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})
