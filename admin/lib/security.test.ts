import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSafeAdminProxyPath,
  clientIpFromForwardedHeaders,
  getLoginRateLimitKey,
  hasValidSameOrigin,
} from './security.ts'

const allowedPrefixes = ['hospitals', 'content', 'reports']

function req(method: string, origin?: string) {
  const headers = new Headers()
  if (origin) headers.set('origin', origin)
  return {
    method,
    headers,
    nextUrl: { origin: 'https://admin.example.test' },
  }
}

test('login rate-limit key prefers the trusted edge header over x-forwarded-for', () => {
  const headers = new Headers({
    'x-vercel-forwarded-for': '203.0.113.2',
    'x-forwarded-for': '203.0.113.1',
  })

  assert.equal(getLoginRateLimitKey({ headers }), 'ip:203.0.113.2')
})

test('login rate-limit key uses the GCP LB client entry (second-from-right XFF)', () => {
  // GCP 외부 ALB 뒤: XFF = "<client-supplied>, <client>, <lb>" — 클라이언트가
  // 임의 항목을 prepend해도 second-from-right(실제 client)가 선택돼야 한다.
  const headers = new Headers({
    'x-forwarded-for': '6.6.6.6, 198.51.100.10, 130.211.0.5',
  })

  assert.equal(getLoginRateLimitKey({ headers }), 'ip:198.51.100.10')
})

test('client IP helper handles minimal LB chain and dev single entry', () => {
  // LB만 거친 정상 체인: "client, lb"
  assert.equal(
    clientIpFromForwardedHeaders(new Headers({ 'x-forwarded-for': '198.51.100.10, 130.211.0.5' })),
    '198.51.100.10',
  )
  // 로컬 dev: 단일 항목
  assert.equal(
    clientIpFromForwardedHeaders(new Headers({ 'x-forwarded-for': '127.0.0.1' })),
    '127.0.0.1',
  )
  // Vercel 호환: 플랫폼 헤더가 우선
  assert.equal(
    clientIpFromForwardedHeaders(
      new Headers({ 'x-vercel-forwarded-for': '203.0.113.2', 'x-forwarded-for': '6.6.6.6, 1.1.1.1' }),
    ),
    '203.0.113.2',
  )
})

test('login rate-limit key fails open (null) when no valid IP header is present', () => {
  assert.equal(getLoginRateLimitKey({ headers: new Headers() }), null)
  assert.equal(getLoginRateLimitKey({ headers: new Headers({ 'x-forwarded-for': 'not-an-ip' }) }), null)
})

test('admin proxy path builder rejects dot segments and path separators', () => {
  assert.equal(buildSafeAdminProxyPath(['hospitals', '..', 'leads'], allowedPrefixes), null)
  assert.equal(buildSafeAdminProxyPath(['hospitals', '.'], allowedPrefixes), null)
  assert.equal(buildSafeAdminProxyPath(['hospitals', 'demo/../../reports'], allowedPrefixes), null)
  assert.equal(buildSafeAdminProxyPath(['hospitals', 'demo\\..\\reports'], allowedPrefixes), null)
})

test('admin proxy path builder allows only configured prefixes and encodes segments', () => {
  assert.equal(buildSafeAdminProxyPath(['leads'], allowedPrefixes), null)
  assert.equal(
    buildSafeAdminProxyPath(['hospitals', 'demo hospital', 'content'], allowedPrefixes),
    'hospitals/demo%20hospital/content',
  )
})

test('same-origin protection applies to state-changing requests', () => {
  assert.equal(hasValidSameOrigin(req('GET')), true)
  assert.equal(hasValidSameOrigin(req('POST', 'https://admin.example.test')), true)
  assert.equal(hasValidSameOrigin(req('PATCH', 'https://evil.example.test')), false)
  assert.equal(hasValidSameOrigin(req('DELETE')), false)
})
