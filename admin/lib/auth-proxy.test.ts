import assert from 'node:assert/strict'
import test from 'node:test'

import { unstable_doesMiddlewareMatch } from 'next/experimental/testing/server.js'

import { buildAdminAuthProxyResponse, adminAuthProxyConfig } from './auth-proxy.ts'
import { generateSessionToken } from './session.ts'

const sessionPayload = {
  accountId: '0f0a41a9-bf2c-4f7b-b182-b85dc729b6e4',
  email: 'owner@example.com',
  name: 'Owner',
  role: 'OWNER',
}

function requestFor(path: string, cookie?: string) {
  const url = new URL(path, 'https://admin.example.test')
  const nextUrl = Object.assign(url, {
    clone() {
      return new URL(url.toString())
    },
  })
  const headers = new Headers()
  if (cookie) {
    headers.set('cookie', cookie)
  }

  return {
    nextUrl,
    cookies: {
      get(name: string) {
        if (name !== 'admin_session' || !cookie) return undefined
        const value = cookie
          .split(';')
          .map((part) => part.trim())
          .find((part) => part.startsWith(`${name}=`))
          ?.slice(`${name}=`.length)
        return value ? { value } : undefined
      },
    },
    headers,
  }
}

test('admin auth proxy matcher excludes framework and metadata assets only', () => {
  const shouldRun = ['/', '/login', '/api/auth/login', '/api/admin/hospitals']
  const shouldSkip = ['/_next/static/app.js', '/favicon.ico', '/robots.txt', '/sitemap.xml']

  for (const url of shouldRun) {
    assert.equal(
      unstable_doesMiddlewareMatch({ config: adminAuthProxyConfig, nextConfig: {}, url }),
      true,
      `${url} should run through proxy`,
    )
  }

  for (const url of shouldSkip) {
    assert.equal(
      unstable_doesMiddlewareMatch({ config: adminAuthProxyConfig, nextConfig: {}, url }),
      false,
      `${url} should skip proxy`,
    )
  }
})

test('admin auth proxy allows login and auth API routes without a session secret', async () => {
  const originalSecret = process.env.ADMIN_SESSION_SECRET
  delete process.env.ADMIN_SESSION_SECRET

  try {
    assert.equal(await buildAdminAuthProxyResponse(requestFor('/login')), undefined)
    assert.equal(await buildAdminAuthProxyResponse(requestFor('/api/auth/login')), undefined)
  } finally {
    if (originalSecret === undefined) {
      delete process.env.ADMIN_SESSION_SECRET
    } else {
      process.env.ADMIN_SESSION_SECRET = originalSecret
    }
  }
})

test('admin auth proxy returns JSON 401 for unauthenticated API routes', async () => {
  process.env.ADMIN_SESSION_SECRET = 'test-secret'

  const res = await buildAdminAuthProxyResponse(requestFor('/api/admin/hospitals'))

  assert.equal(res?.status, 401)
  assert.deepEqual(await res?.json(), { error: 'Unauthorized' })
})

test('admin auth proxy redirects pages to login with the original path and clears stale sessions', async () => {
  process.env.ADMIN_SESSION_SECRET = 'test-secret'

  const res = await buildAdminAuthProxyResponse(
    requestFor('/hospitals/demo/content?status=DRAFT', 'admin_session=bad-token'),
  )

  assert.equal(res?.status, 307)
  assert.equal(
    res?.headers.get('location'),
    'https://admin.example.test/login?redirect=%2Fhospitals%2Fdemo%2Fcontent%3Fstatus%3DDRAFT',
  )
  assert.match(res?.headers.get('set-cookie') ?? '', /admin_session=;/)
})

test('admin auth proxy allows requests with a valid session token', async () => {
  process.env.ADMIN_SESSION_SECRET = 'test-secret'
  const token = await generateSessionToken('test-secret', 60, sessionPayload)

  const res = await buildAdminAuthProxyResponse(requestFor('/hospitals', `admin_session=${token}`))

  assert.equal(res, undefined)
})
