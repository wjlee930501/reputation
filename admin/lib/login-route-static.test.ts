import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { NextRequest } from 'next/server.js'

import { ADMIN_CSRF_COOKIE_NAME } from './csrf.ts'
import { handleAdminLogin } from './login-route.ts'

const loginPageSource = readFileSync(join(process.cwd(), 'app/login/page.tsx'), 'utf8')

const ORIGINAL_FETCH = globalThis.fetch
const ORIGINAL_ENV = {
  ADMIN_SECRET_KEY: process.env.ADMIN_SECRET_KEY,
  ADMIN_SESSION_SECRET: process.env.ADMIN_SESSION_SECRET,
  BACKEND_URL: process.env.BACKEND_URL,
  NODE_ENV: process.env.NODE_ENV,
}

function setEnv(name: string, value: string | undefined) {
  if (value === undefined) {
    Reflect.deleteProperty(process.env, name)
    return
  }
  Object.defineProperty(process.env, name, {
    configurable: true,
    enumerable: true,
    value,
    writable: true,
  })
}

function restoreEnv() {
  for (const [key, value] of Object.entries(ORIGINAL_ENV)) {
    setEnv(key, value)
  }
  globalThis.fetch = ORIGINAL_FETCH
}

function configureEnv() {
  setEnv('ADMIN_SECRET_KEY', 'test-admin-key')
  setEnv('ADMIN_SESSION_SECRET', 'test-session-secret')
  setEnv('BACKEND_URL', 'https://backend.example.test')
  setEnv('NODE_ENV', 'test')
}

function loginRequest(
  body: Record<string, unknown> = { email: 'Owner@Example.com', password: 'secret' },
  headers: Record<string, string> = {},
): NextRequest {
  return new NextRequest('https://admin.example.test/api/auth/login', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      host: 'admin.example.test',
      origin: 'https://admin.example.test',
      ...headers,
    },
    body: JSON.stringify(body),
  })
}

test.afterEach(restoreEnv)

test('login route forwards upstream 429 without minting cookies', async () => {
  configureEnv()
  globalThis.fetch = (async () => new Response('too many', { status: 429 })) as typeof fetch

  const res = await handleAdminLogin(loginRequest())

  assert.equal(res.status, 429)
  assert.deepEqual(await res.json(), { error: 'Too many login attempts' })
  assert.equal(res.headers.get('set-cookie'), null)
})

test('login route rejects malformed backend success bodies before setting cookies', async () => {
  configureEnv()
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ id: 'acct-1' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as typeof fetch

  const res = await handleAdminLogin(loginRequest())

  assert.equal(res.status, 502)
  assert.deepEqual(await res.json(), { error: 'Invalid auth response' })
  assert.equal(res.headers.get('set-cookie'), null)
})

test('login route rejects state-changing requests from the wrong origin', async () => {
  configureEnv()
  let fetchCalled = false
  globalThis.fetch = (async () => {
    fetchCalled = true
    return new Response('{}', { status: 200 })
  }) as typeof fetch

  const res = await handleAdminLogin(loginRequest(undefined, { origin: 'https://evil.example.test' }))

  assert.equal(res.status, 403)
  assert.equal(await res.text(), 'Forbidden')
  assert.equal(fetchCalled, false)
})

test('login route mints bounded session and CSRF cookies for valid backend accounts', async () => {
  configureEnv()
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        id: 'acct-1',
        email: 'owner@example.com',
        name: 'Owner',
        role: 'OWNER',
      }),
      {
        status: 200,
        headers: { 'content-type': 'application/json' },
      },
    )) as typeof fetch

  const res = await handleAdminLogin(loginRequest())

  assert.equal(res.status, 200)
  assert.deepEqual(await res.json(), {
    ok: true,
    account: { email: 'owner@example.com', name: 'Owner', role: 'OWNER' },
  })
  const setCookie = res.headers.get('set-cookie') ?? ''
  assert.match(setCookie, /admin_session=/)
  assert.match(setCookie, /HttpOnly/)
  assert.match(setCookie, /SameSite=lax/i)
  assert.match(setCookie, /Max-Age=604800/)
  assert.match(setCookie, new RegExp(`${ADMIN_CSRF_COOKIE_NAME}=`))
})

test('login page announces authentication errors accessibly', () => {
  assert.match(loginPageSource, /role=["']alert["']/)
  assert.match(loginPageSource, /aria-live=["']polite["']/)
})
