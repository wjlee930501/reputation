import assert from 'node:assert/strict'
import test from 'node:test'

import { NextRequest } from 'next/server.js'

import {
  __clearDomainSlugCacheForTest,
  __setDomainSlugCacheEntryForTest,
  proxy as middleware,
} from '../proxy.ts'

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

test('clinic-qualified llms routes enforce custom-host ownership', async () => {
  resetEnvAndCache()
  globalThis.fetch = async () => Response.json({ slug: 'clinic-a' })
  const foreign = await middleware(requestFor('a.example.com', '/clinic-b/llms.txt'))
  assert.equal(foreign.status, 404)
  const own = await middleware(requestFor('a.example.com', '/llms.txt'))
  assert.equal(new URL(own.headers.get('x-middleware-rewrite') ?? '').pathname, '/clinic-a/llms.txt')
  const qualified = await middleware(requestFor('a.example.com', '/clinic-a/llms.txt'))
  assert.equal(qualified.status, 308)
  assert.equal(new URL(qualified.headers.get('location') ?? '').pathname, '/llms.txt')
  const platform = await middleware(requestFor('reputation.motionlabs.kr', '/clinic-b/llms.txt'))
  assert.equal(platform.headers.get('x-middleware-next'), '1')
})

test('unknown custom hosts cannot serve clinic-qualified llms routes', async () => {
  resetEnvAndCache()
  globalThis.fetch = async () => new Response('Not found', { status: 404 })
  const response = await middleware(requestFor('unknown.example.com', '/clinic-b/llms.txt'))
  assert.equal(response.status, 404)
})

function byDomainFetch(records: Record<string, { slug: string; aeo_domain: string | null }>): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const host = decodeURIComponent(url.slice(url.lastIndexOf('/') + 1))
    const record = records[host]
    if (!record) return new Response('Not found', { status: 404 })
    return Response.json({ slug: record.slug, name: 'clinic', aeo_domain: record.aeo_domain })
  }) as typeof fetch
}

test('platform /{slug} paths move permanently to the managed subdomain', async () => {
  resetEnvAndCache()
  globalThis.fetch = byDomainFetch({
    'jang-clinic.reputation.motionlabs.kr': { slug: 'jang-clinic', aeo_domain: null },
  })

  const hub = await middleware(requestFor('reputation.motionlabs.kr', '/jang-clinic'))
  const article = await middleware(
    requestFor('reputation.motionlabs.kr', '/jang-clinic/contents/abc?utm_source=chatgpt.com'),
  )
  const llms = await middleware(requestFor('reputation.motionlabs.kr', '/jang-clinic/llms.txt'))

  assert.equal(hub.status, 308)
  assert.equal(hub.headers.get('location'), 'https://jang-clinic.reputation.motionlabs.kr/')
  assert.equal(article.status, 308)
  assert.equal(
    article.headers.get('location'),
    'https://jang-clinic.reputation.motionlabs.kr/contents/abc?utm_source=chatgpt.com',
  )
  assert.equal(llms.headers.get('location'), 'https://jang-clinic.reputation.motionlabs.kr/llms.txt')
})

test('platform /{slug} paths move to the custom domain when the clinic serves one', async () => {
  resetEnvAndCache()
  globalThis.fetch = byDomainFetch({
    'jang-clinic.reputation.motionlabs.kr': { slug: 'jang-clinic', aeo_domain: 'jangclinic.co.kr' },
  })

  const res = await middleware(requestFor('reputation.motionlabs.kr', '/jang-clinic/visit'))

  assert.equal(res.status, 308)
  assert.equal(res.headers.get('location'), 'https://jangclinic.co.kr/visit')
})

test('managed subdomain moves to the custom domain; the custom domain itself is served', async () => {
  resetEnvAndCache()
  globalThis.fetch = byDomainFetch({
    'jang-clinic.reputation.motionlabs.kr': { slug: 'jang-clinic', aeo_domain: 'jangclinic.co.kr' },
    'jangclinic.co.kr': { slug: 'jang-clinic', aeo_domain: 'jangclinic.co.kr' },
  })

  const sub = await middleware(requestFor('jang-clinic.reputation.motionlabs.kr', '/contents'))
  const subInternal = await middleware(
    requestFor('jang-clinic.reputation.motionlabs.kr', '/jang-clinic/doctor'),
  )
  const custom = await middleware(requestFor('jangclinic.co.kr', '/contents'))
  const reserved = await middleware(
    requestFor('jang-clinic.reputation.motionlabs.kr', '/.well-known/reputation-health'),
  )

  assert.equal(sub.status, 308)
  assert.equal(sub.headers.get('location'), 'https://jangclinic.co.kr/contents')
  assert.equal(subInternal.headers.get('location'), 'https://jangclinic.co.kr/doctor')
  assert.equal(custom.status, 200)
  assert.match(custom.headers.get('x-middleware-rewrite') ?? '', /\/jang-clinic\/contents$/)
  assert.equal(reserved.status, 200)
  assert.equal(reserved.headers.get('location'), null)
})

test('platform routes, unknown slugs and lookup outages are left alone', async () => {
  resetEnvAndCache()
  let calls = 0
  const inner = byDomainFetch({})
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls += 1
    return inner(input, init)
  }) as typeof fetch

  for (const path of ['/', '/ai-diagnosis', '/contact', '/privacy', '/llms.txt', '/favicon/jang']) {
    const res = await middleware(requestFor('reputation.motionlabs.kr', path))
    assert.equal(res.headers.get('location'), null, path)
  }
  assert.equal(calls, 0)

  const unknown = await middleware(requestFor('reputation.motionlabs.kr', '/no-such-clinic'))
  assert.equal(unknown.headers.get('location'), null)

  resetEnvAndCache()
  globalThis.fetch = (async () => new Response('down', { status: 500 })) as typeof fetch
  const outage = await middleware(requestFor('reputation.motionlabs.kr', '/jang-clinic'))
  assert.equal(outage.headers.get('location'), null)
})

test('canonical host redirect kill switch keeps the previous behaviour', async () => {
  resetEnvAndCache()
  process.env.SITE_CANONICAL_HOST_REDIRECT = '0'
  globalThis.fetch = byDomainFetch({
    'jang-clinic.reputation.motionlabs.kr': { slug: 'jang-clinic', aeo_domain: 'jangclinic.co.kr' },
  })
  try {
    const platform = await middleware(requestFor('reputation.motionlabs.kr', '/jang-clinic'))
    const sub = await middleware(requestFor('jang-clinic.reputation.motionlabs.kr', '/contents'))
    assert.equal(platform.headers.get('location'), null)
    assert.equal(sub.status, 200)
    assert.match(sub.headers.get('x-middleware-rewrite') ?? '', /\/jang-clinic\/contents$/)
  } finally {
    Reflect.deleteProperty(process.env, 'SITE_CANONICAL_HOST_REDIRECT')
  }
})
