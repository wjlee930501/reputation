import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { IMAGE_WARMUP_BUDGET_MS, imageWarmupSlug, imageWarmupOrigin, imageWarmupUrls, warmClinicHeroImages } from './clinic-image-warmup.ts'

const environment = { K_SERVICE: 'reputation-site', PORT: '8080' }

test('only an explicit clinic home path is eligible, not platform or article-only paths', () => {
  assert.equal(imageWarmupSlug(['/api', '/privacy', '/clinic/contents/a', '//evil.test']), null)
  assert.equal(imageWarmupSlug(['/', '/robots.txt', '/clinic', '/other']), 'clinic')
})

test('warmup origin is fixed to the owned Cloud Run loopback listener', () => {
  assert.equal(imageWarmupOrigin(environment), 'http://127.0.0.1:8080')
  for (const env of [{}, { K_SERVICE: 'reputation-admin', PORT: '8080' }, { ...environment, PORT: '0' }, { ...environment, PORT: '65536' }, { ...environment, PORT: '8080@evil.test' }]) assert.equal(imageWarmupOrigin(env), null)
  assert.ok(IMAGE_WARMUP_BUDGET_MS <= 2500)
})

test('two bounded widths preserve the exact source and version identity', () => {
  const source = 'https://reputation.motionlabs.kr/api/v1/public/hospitals/clinic/assets/id?v=certified'
  const urls = imageWarmupUrls('http://127.0.0.1:8080', source).map(value => new URL(value))
  assert.deepEqual(urls.map(url => url.searchParams.get('w')), ['750', '1200'])
  assert.ok(urls.every(url => url.searchParams.get('url') === source && url.searchParams.get('q') === '75'))
})

test('authorized warmup caps fan-out, uses shared deadlines and sends no caller credentials', async () => {
  const requests: Array<{ url: URL; init?: RequestInit }> = [], slugs: string[] = []
  const dependencies = {
    hero: async (slug: string) => { slugs.push(slug); return 'https://reputation.motionlabs.kr/api/v1/public/hospitals/clinic/assets/warm-test' },
    fetch: (async (input: string | URL | Request, init?: RequestInit) => {
      requests.push({ url: new URL(String(input)), init })
      return new Response('image', { headers: { 'content-type': 'image/avif' } })
    }) as typeof fetch,
  }
  assert.equal(await warmClinicHeroImages(['/clinic', '/other'], environment, dependencies), 2)
  assert.deepEqual(slugs, ['clinic'])
  assert.equal(requests.length, 2)
  assert.ok(requests.every(request => request.url.origin === 'http://127.0.0.1:8080'))
  assert.equal(requests[0].init?.signal, requests[1].init?.signal)
  assert.deepEqual(Object.keys(requests[0].init?.headers ?? {}), ['Accept'])
  assert.equal(await warmClinicHeroImages(['/clinic'], environment, dependencies), 0)
  assert.equal(requests.length, 2, 'Repeated invalidation must not duplicate conversion requests')
})

test('disabled, withdrawn, off-allowlist and failed lookups never fetch image bytes', async () => {
  let calls = 0
  const fetcher = (async () => { calls++; return new Response('') }) as typeof fetch
  for (const hero of [async () => null, async () => 'https://evil.test/photo', async () => { throw new Error('hospital unavailable') }]) {
    assert.equal(await warmClinicHeroImages(['/clinic'], environment, { fetch: fetcher, hero }), 0)
  }
  assert.equal(await warmClinicHeroImages(['/clinic'], { K_SERVICE: undefined, PORT: undefined }, { fetch: fetcher, hero: async () => { throw new Error('must not call') } }), 0)
  assert.equal(calls, 0)
})

test('network failures are best effort and do not invalidate the successful cache operation', async () => {
  const result = await warmClinicHeroImages(['/clinic'], environment, {
    hero: async () => 'https://reputation.motionlabs.kr/api/v1/public/hospitals/clinic/assets/failure-test',
    fetch: (async () => { throw new Error('optimizer unavailable') }) as typeof fetch,
  })
  assert.equal(result, 0)
})

test('warmup runs after authentication and immediate expiry, never as an unauthenticated endpoint', () => {
  const route = readFileSync(new URL('../app/api/revalidate/route.ts', import.meta.url), 'utf8')
  assert.ok(route.indexOf('await warmClinicHeroImages(paths)') > route.indexOf('revalidateTag(tag, { expire: 0 })'))
  assert.ok(route.indexOf('await warmClinicHeroImages(paths)') > route.indexOf('revalidatePath(path)'))
  assert.ok(route.indexOf('await warmClinicHeroImages(paths)') > route.indexOf('constantTimeEqual(provided, SECRET)'))
})
