import assert from 'node:assert/strict'
import test from 'node:test'

import { NextRequest } from 'next/server.js'

import {
  __clearDomainSlugCacheForTest,
  __setDomainSlugCacheEntryForTest,
  middleware,
} from '../middleware.ts'

const ORIGINAL_FETCH = globalThis.fetch
const ORIGINAL_SITE_URL = process.env.NEXT_PUBLIC_SITE_URL
const ORIGINAL_API_URL = process.env.NEXT_PUBLIC_API_URL

function requestFor(hostname: string, pathname = '/'): NextRequest {
  return new NextRequest(`https://${hostname}${pathname}`, {
    headers: { host: hostname },
  })
}

function resetEnvAndCache(): void {
  process.env.NEXT_PUBLIC_SITE_URL = 'https://reputation.motionlabs.kr'
  process.env.NEXT_PUBLIC_API_URL = 'https://backend.example.test/api/v1/public'
  globalThis.fetch = ORIGINAL_FETCH
  __clearDomainSlugCacheForTest()
}

test.after(() => {
  if (ORIGINAL_SITE_URL === undefined) {
    Reflect.deleteProperty(process.env, 'NEXT_PUBLIC_SITE_URL')
  } else {
    process.env.NEXT_PUBLIC_SITE_URL = ORIGINAL_SITE_URL
  }
  if (ORIGINAL_API_URL === undefined) {
    Reflect.deleteProperty(process.env, 'NEXT_PUBLIC_API_URL')
  } else {
    process.env.NEXT_PUBLIC_API_URL = ORIGINAL_API_URL
  }
  globalThis.fetch = ORIGINAL_FETCH
})

test('custom domain lookup true 404 stays a 404', async () => {
  resetEnvAndCache()
  const fetchMock: typeof fetch = async () => new Response('Not found', { status: 404 })
  globalThis.fetch = fetchMock

  const res = await middleware(requestFor('clinic.example.com', '/contents'))

  assert.equal(res.status, 404)
  assert.equal(await res.text(), 'Not found')
})

test('custom domain lookup outage without cache returns no-store 503', async () => {
  resetEnvAndCache()
  const fetchMock: typeof fetch = async () => new Response('upstream unavailable', { status: 500 })
  globalThis.fetch = fetchMock

  const res = await middleware(requestFor('clinic.example.com', '/contents'))

  assert.equal(res.status, 503)
  assert.equal(res.headers.get('cache-control'), 'no-store')
  assert.equal(res.headers.get('retry-after'), '30')
})

test('custom domain lookup outage uses fresh cached slug for the same hostname only', async () => {
  resetEnvAndCache()
  __setDomainSlugCacheEntryForTest('clinic.example.com', {
    slug: 'jang-clinic',
    freshUntil: Date.now() + 60_000,
    staleUntil: Date.now() + 86_400_000,
  })
  const fetchMock: typeof fetch = async () => new Response('upstream unavailable', { status: 500 })
  globalThis.fetch = fetchMock

  const cachedRes = await middleware(requestFor('clinic.example.com', '/contents'))
  const otherHostRes = await middleware(requestFor('other-clinic.example.com', '/contents'))

  assert.equal(cachedRes.status, 200)
  assert.match(cachedRes.headers.get('x-middleware-rewrite') ?? '', /\/jang-clinic\/contents$/)
  assert.equal(otherHostRes.status, 503)
})

test('custom domain lookup outage uses expired-fresh slug inside the 24h stale window', async () => {
  resetEnvAndCache()
  __setDomainSlugCacheEntryForTest('clinic.example.com', {
    slug: 'old-clinic',
    freshUntil: Date.now() - 1,
    staleUntil: Date.now() + 60_000,
  })
  const fetchMock: typeof fetch = async () => new Response('upstream unavailable', { status: 500 })
  globalThis.fetch = fetchMock

  const res = await middleware(requestFor('clinic.example.com', '/contents'))

  assert.equal(res.status, 200)
  assert.match(res.headers.get('x-middleware-rewrite') ?? '', /\/old-clinic\/contents$/)
})

test('a spoofed x-forwarded-host on the Cloud Run host never impersonates a tenant', async () => {
  resetEnvAndCache()
  // GCLB → Cloud Run은 x-forwarded-host를 덮어쓰지 않으므로 이 값은 클라이언트가 보낸 것일 수 있다.
  const request = new NextRequest('https://site-abc123-du.a.run.app/contents', {
    headers: {
      host: 'site-abc123-du.a.run.app',
      'x-forwarded-host': 'clinic.example.com',
    },
  })
  const fetchMock: typeof fetch = async () =>
    new Response(JSON.stringify({ slug: 'jang-clinic' }), { status: 200 })
  globalThis.fetch = fetchMock

  const res = await middleware(request)

  assert.equal(res.status, 200)
  assert.equal(res.headers.get('x-middleware-rewrite'), null, '병원 허브로 rewrite되면 안 된다')
})

test('custom domain lookup outage rejects positive slug beyond stale window', async () => {
  resetEnvAndCache()
  __setDomainSlugCacheEntryForTest('clinic.example.com', {
    slug: 'old-clinic',
    freshUntil: Date.now() - 60_000,
    staleUntil: Date.now() - 1,
  })
  const fetchMock: typeof fetch = async () => new Response('upstream unavailable', { status: 500 })
  globalThis.fetch = fetchMock

  const res = await middleware(requestFor('clinic.example.com', '/contents'))

  assert.equal(res.status, 503)
  assert.equal(res.headers.get('cache-control'), 'no-store')
  assert.equal(res.headers.get('retry-after'), '30')
})
