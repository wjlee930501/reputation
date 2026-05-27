import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSafeAdminProxyPath,
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

test('login rate-limit key ignores spoofable forwarding headers', () => {
  const headers = new Headers({
    'x-forwarded-for': '203.0.113.1',
    'x-real-ip': '203.0.113.2',
  })

  assert.equal(getLoginRateLimitKey({ headers }), 'global')
})

test('login rate-limit key uses runtime IP when the platform provides one', () => {
  const request = {
    headers: new Headers({ 'x-forwarded-for': '203.0.113.1' }),
    ip: '198.51.100.10',
  }

  assert.equal(getLoginRateLimitKey(request), 'ip:198.51.100.10')
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
