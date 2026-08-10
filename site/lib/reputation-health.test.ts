import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveReputationHealth } from './reputation-health.ts'

const ORIGINAL_API_URL = process.env.NEXT_PUBLIC_API_URL
const ORIGINAL_SITE_URL = process.env.NEXT_PUBLIC_SITE_URL
const ORIGINAL_RELEASE = process.env.K_REVISION

function requestHeaders(host: string): Headers {
  return new Headers({ host })
}

test.beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = 'https://backend.example.test/api/v1/public'
  process.env.NEXT_PUBLIC_SITE_URL = 'https://reputation.motionlabs.kr'
  process.env.K_REVISION = 'site-r17'
})

test.after(() => {
  for (const [name, value] of [
    ['NEXT_PUBLIC_API_URL', ORIGINAL_API_URL],
    ['NEXT_PUBLIC_SITE_URL', ORIGINAL_SITE_URL],
    ['K_REVISION', ORIGINAL_RELEASE],
  ] as const) {
    if (value === undefined) Reflect.deleteProperty(process.env, name)
    else process.env[name] = value
  }
})

test('custom tenant health returns the exact no-store identity marker', async () => {
  let requestedUrl = ''
  let requestedInit: RequestInit | undefined
  const response = await resolveReputationHealth(
    requestHeaders('Clinic.Example.COM:443'),
    async (input, init) => {
      requestedUrl = String(input)
      requestedInit = init
      return Response.json({
        hospital_id: 'd28562b9-cfad-4de8-a223-e21e331157d9',
        slug: 'jang-clinic',
        canonical_host: 'clinic.example.com',
      })
    },
  )

  assert.equal(response.status, 200)
  assert.equal(response.headers.get('cache-control'), 'no-store, private')
  assert.equal(requestedInit?.redirect, 'manual')
  assert.equal(requestedInit?.cache, 'no-store')
  assert.match(requestedUrl, /health\/by-domain\/clinic\.example\.com$/)
  assert.deepEqual(await response.json(), {
    hospital_id: 'd28562b9-cfad-4de8-a223-e21e331157d9',
    slug: 'jang-clinic',
    canonical_host: 'clinic.example.com',
    release: 'site-r17',
  })
})

test('unmapped and wrong-host tenant markers never report healthy', async () => {
  const notFound = await resolveReputationHealth(
    requestHeaders('missing.example.com'),
    async () => new Response('missing', { status: 404 }),
  )
  const mismatch = await resolveReputationHealth(
    requestHeaders('clinic.example.com'),
    async () => Response.json({
      hospital_id: 'd28562b9-cfad-4de8-a223-e21e331157d9',
      slug: 'jang-clinic',
      canonical_host: 'other.example.com',
    }),
  )

  assert.equal(notFound.status, 404)
  assert.equal(mismatch.status, 502)
  assert.equal(notFound.headers.get('cache-control'), 'no-store, private')
  assert.equal(mismatch.headers.get('cache-control'), 'no-store, private')
})

test('malformed tenant lookup never reports healthy or becomes cacheable', async () => {
  process.env.NEXT_PUBLIC_API_URL = 'https://api.example.com/api/v1/public'
  const response = await resolveReputationHealth(
    new Headers({ host: 'clinic.example.com' }),
    async () => new Response('<html>gateway error</html>', { status: 200 }),
  )

  assert.equal(response.status, 502)
  assert.equal(response.headers.get('cache-control'), 'no-store, private')
})

test('platform host does not expose a tenant marker or call the backend', async () => {
  let called = false
  const response = await resolveReputationHealth(
    requestHeaders('reputation.motionlabs.kr'),
    async () => {
      called = true
      return new Response()
    },
  )

  assert.equal(response.status, 404)
  assert.equal(called, false)
})
