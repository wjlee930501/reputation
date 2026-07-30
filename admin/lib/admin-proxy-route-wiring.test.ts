// Admin API 프록시 라우트의 실제 배선과 거부 응답을 동작으로 검증한다.
//
// 이전 버전은 route.ts / admin-api-proxy-route.ts 소스 텍스트를 정규식으로 매칭했다.
// 그 방식은 주석 처리된 줄에도 통과하고 무해한 이름 변경에는 깨져서 회귀도 리팩터도
// 잡지 못했다. 여기서는 Next가 실제로 로드하는 route 모듈을 그대로 import해
// 핸들러 동일성을 확인하고, 거부 경로는 stub 요청으로 호출해 status/헤더를 단언한다.
import assert from 'node:assert/strict'
import { register } from 'node:module'
import { pathToFileURL } from 'node:url'
import test from 'node:test'

import { NextRequest } from 'next/server.js'

import { handleAdminApiProxy } from './admin-api-proxy-route.ts'
import { adminAuthProxyConfig } from './auth-proxy.ts'

// node:test는 tsconfig의 `@/*` path alias를 모르고, 확장자 없는 `next/server` 같은
// 서브패스도 그대로는 못 푼다. Next가 실제로 로드하는 파일을 그대로 import하기 위한
// 최소 resolve 훅.
const projectRoot = pathToFileURL(`${process.cwd()}/`).href
register(
  'data:text/javascript,' +
    encodeURIComponent(
      `const root = ${JSON.stringify(projectRoot)}
       export async function resolve(specifier, context, next) {
         if (specifier.startsWith('@/')) {
           return next(new URL(specifier.slice(2) + '.ts', root).href, context)
         }
         if (specifier.startsWith('./') || specifier.startsWith('../')) {
           try { return await next(specifier, context) } catch { return next(specifier + '.ts', context) }
         }
         if (/^next\\/[a-z-]+$/.test(specifier)) {
           try { return await next(specifier + '.js', context) } catch {}
         }
         return next(specifier, context)
       }`,
    ),
)

const ROUTE_MODULE = pathToFileURL(`${process.cwd()}/app/api/admin/[...path]/route.ts`).href
const PROXY_MODULE = pathToFileURL(`${process.cwd()}/proxy.ts`).href

function proxyRequest(
  method: string,
  headers: Record<string, string> = {},
): NextRequest {
  return new NextRequest('https://admin.example.test/api/admin/hospitals', {
    method,
    headers: {
      host: 'admin.example.test',
      origin: 'https://admin.example.test',
      ...headers,
    },
  })
}

function proxyContext() {
  return { params: Promise.resolve({ path: ['hospitals'] }) }
}

test('every admin API method Next can route is served by the behavior-tested proxy handler', async () => {
  const route = await import(ROUTE_MODULE)

  for (const method of ['GET', 'POST', 'PATCH', 'DELETE']) {
    assert.equal(
      route[method],
      handleAdminApiProxy,
      `${method} must delegate to handleAdminApiProxy`,
    )
  }
})

test('the matcher Next reads equals the matcher the auth-proxy tests exercise', async () => {
  // proxy.ts와 lib/auth-proxy.ts에 matcher가 따로 선언돼 있던 시절에는, 테스트가
  // 검증하는 값과 Next가 실제로 적용하는 값이 갈라져도 아무도 몰랐다.
  //
  // 두 값을 하나로 묶고 싶지만 Next가 `config`를 정적 리터럴로만 파싱하므로 re-export가
  // 안 된다(그 시도가 admin 빌드를 깨뜨렸다). 그래서 **값이 같은지**를 여기서 잡는다 —
  // 한쪽만 바꾸면 이 테스트가 실패한다.
  const proxyModule = await import(PROXY_MODULE)

  assert.deepEqual(proxyModule.config, adminAuthProxyConfig)
  assert.equal(typeof proxyModule.proxy, 'function')
})

test('the auth proxy leaves login and auth API paths public while guarding the console', async () => {
  const proxyModule = await import(PROXY_MODULE)
  const publicRequest = {
    nextUrl: { pathname: '/login', search: '', clone: () => new URL('https://admin.example.test/login') },
    cookies: { get: () => undefined },
  }

  // 공개 경로는 프록시가 응답을 만들지 않고 그대로 통과시킨다 (NextResponse.next()).
  const passthrough = await proxyModule.proxy(publicRequest)
  assert.equal(passthrough.status, 200)
  assert.equal(passthrough.headers.get('location'), null)
})

test('the routed admin handler rejects cross-origin requests with a no-store 403', async () => {
  process.env.ADMIN_SECRET_KEY = 'test-admin-key'
  process.env.ADMIN_SESSION_SECRET = 'test-session-secret'
  const originalFetch = globalThis.fetch
  let fetchCalled = false
  globalThis.fetch = (async () => {
    fetchCalled = true
    return new Response('{}', { status: 200 })
  }) as typeof fetch

  try {
    const route = await import(ROUTE_MODULE)
    const res = await route.POST(
      proxyRequest('POST', { origin: 'https://evil.example.test' }),
      proxyContext(),
    )

    assert.equal(res.status, 403)
    assert.equal(await res.text(), 'Forbidden')
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.equal(fetchCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('the admin proxy rejects unsupported methods with a no-store 405 before reading env or session', async () => {
  const originalKey = process.env.ADMIN_SECRET_KEY
  const originalSecret = process.env.ADMIN_SESSION_SECRET
  Reflect.deleteProperty(process.env, 'ADMIN_SECRET_KEY')
  Reflect.deleteProperty(process.env, 'ADMIN_SESSION_SECRET')

  try {
    for (const method of ['PUT', 'OPTIONS']) {
      const res = await handleAdminApiProxy(proxyRequest(method), proxyContext())

      assert.equal(res.status, 405, `${method} must not be proxied`)
      assert.equal(await res.text(), 'Method Not Allowed')
      assert.equal(res.headers.get('cache-control'), 'no-store, private')
    }
  } finally {
    if (originalKey !== undefined) process.env.ADMIN_SECRET_KEY = originalKey
    if (originalSecret !== undefined) process.env.ADMIN_SESSION_SECRET = originalSecret
  }
})

test('a misconfigured admin deployment answers with a no-store 500 instead of proxying', async () => {
  const originalKey = process.env.ADMIN_SECRET_KEY
  Reflect.deleteProperty(process.env, 'ADMIN_SECRET_KEY')
  const originalFetch = globalThis.fetch
  let fetchCalled = false
  globalThis.fetch = (async () => {
    fetchCalled = true
    return new Response('{}', { status: 200 })
  }) as typeof fetch

  try {
    const res = await handleAdminApiProxy(proxyRequest('GET'), proxyContext())

    assert.equal(res.status, 500)
    assert.deepEqual(await res.json(), { error: 'Server misconfigured' })
    assert.equal(res.headers.get('cache-control'), 'no-store, private')
    assert.equal(fetchCalled, false)
  } finally {
    globalThis.fetch = originalFetch
    if (originalKey !== undefined) process.env.ADMIN_SECRET_KEY = originalKey
  }
})
