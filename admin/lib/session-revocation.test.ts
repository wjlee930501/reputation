import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ADMIN_SESSION_REVOCATION_CACHE_MAX_ENTRIES,
  ADMIN_SESSION_REVOCATION_TIMEOUT_MS,
  adminSessionRevocationCacheSize,
  checkAdminSessionRevocation,
  checkAdminSessionRevocationCached,
  clearAdminSessionRevocationCache,
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

test('cached revocation checks reuse an "active" result within the TTL', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch
  let fetchCount = 0

  const fetchMock: typeof fetch = async () => {
    fetchCount += 1
    return new Response(JSON.stringify({ revoked: false }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const first = await checkAdminSessionRevocationCached({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken: 'repeat-token',
    })
    const second = await checkAdminSessionRevocationCached({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken: 'repeat-token',
    })

    assert.equal(first, 'active')
    assert.equal(second, 'active')
    assert.equal(fetchCount, 1, '두 번째 확인은 캐시를 사용해 백엔드로 왕복하지 않아야 한다')
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})

test('cached revocation checks never cache a "revoked" result', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch
  let fetchCount = 0

  const fetchMock: typeof fetch = async () => {
    fetchCount += 1
    return new Response(JSON.stringify({ revoked: true }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  try {
    const first = await checkAdminSessionRevocationCached({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken: 'revoked-token',
    })
    const second = await checkAdminSessionRevocationCached({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken: 'revoked-token',
    })

    assert.equal(first, 'revoked')
    assert.equal(second, 'revoked')
    assert.equal(fetchCount, 2, '폐기 결과는 캐시하지 않고 매번 다시 확인해야 한다')
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})

test('revoking a session drops its cached "active" entry so the next check re-hits the backend', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch
  let revocationChecks = 0

  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.endsWith('/sessions/revoke')) {
      return new Response(JSON.stringify({ revoked: true }), { status: 200 })
    }
    // 폐기 확인 왕복 (sessions/{hash}/revocation)
    revocationChecks += 1
    return new Response(JSON.stringify({ revoked: false }), { status: 200 })
  }
  globalThis.fetch = fetchMock

  const options = {
    backendUrl: 'https://backend.example.test',
    adminKey: 'test-admin-key',
    sessionToken: 'logout-token',
  }

  try {
    // 1) 'active' 캐시 워밍
    assert.equal(await checkAdminSessionRevocationCached(options), 'active')
    assert.equal(revocationChecks, 1)

    // 2) TTL 내 재확인은 캐시를 사용해 백엔드로 왕복하지 않는다
    assert.equal(await checkAdminSessionRevocationCached(options), 'active')
    assert.equal(revocationChecks, 1)

    // 3) 로그아웃 → 폐기 성공 시 해당 토큰의 캐시 엔트리를 즉시 무효화한다
    const revoked = await revokeAdminSession({ ...options, expiresAtMs: Date.now() + 60_000 })
    assert.equal(revoked, 'revoked')

    // 4) 로그아웃 직후 같은 토큰 재확인은 캐시가 비워져 백엔드로 재조회해야 한다
    assert.equal(await checkAdminSessionRevocationCached(options), 'active')
    assert.equal(revocationChecks, 2, '로그아웃 후에는 캐시가 무효화되어 백엔드로 재조회해야 한다')
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})

test('the active-revocation cache is bounded and evicts the oldest entries past the cap', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch

  const fetchMock: typeof fetch = async () =>
    new Response(JSON.stringify({ revoked: false }), { status: 200 })
  globalThis.fetch = fetchMock

  try {
    const overCap = ADMIN_SESSION_REVOCATION_CACHE_MAX_ENTRIES + 25
    for (let i = 0; i < overCap; i += 1) {
      await checkAdminSessionRevocationCached({
        backendUrl: 'https://backend.example.test',
        adminKey: 'test-admin-key',
        sessionToken: `token-${i}`,
      })
    }

    assert.ok(
      adminSessionRevocationCacheSize() <= ADMIN_SESSION_REVOCATION_CACHE_MAX_ENTRIES,
      `캐시 크기(${adminSessionRevocationCacheSize()})는 상한(${ADMIN_SESSION_REVOCATION_CACHE_MAX_ENTRIES})을 넘지 않아야 한다`,
    )
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})

test('cached revocation checks never cache an "unavailable" result', async () => {
  clearAdminSessionRevocationCache()
  const originalFetch = globalThis.fetch
  let fetchCount = 0

  const fetchMock: typeof fetch = async () => {
    fetchCount += 1
    throw new DOMException('deadline', 'TimeoutError')
  }
  globalThis.fetch = fetchMock

  try {
    const first = await checkAdminSessionRevocationCached({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken: 'unavailable-token',
    })
    const second = await checkAdminSessionRevocationCached({
      backendUrl: 'https://backend.example.test',
      adminKey: 'test-admin-key',
      sessionToken: 'unavailable-token',
    })

    assert.equal(first, 'unavailable')
    assert.equal(second, 'unavailable')
    assert.equal(fetchCount, 2, '확인 실패는 캐시하지 않아 fail-closed 동작을 유지해야 한다')
  } finally {
    globalThis.fetch = originalFetch
    clearAdminSessionRevocationCache()
  }
})
