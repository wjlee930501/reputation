import assert from 'node:assert/strict'
import test from 'node:test'

import { clientIpFromForwardedHeaders } from './client-ip.ts'

test('GCP LB 체인에서 second-from-right 항목(실제 방문자)을 선택한다', () => {
  // 클라이언트가 위조 항목을 prepend해도 LB가 붙인 client 항목이 이긴다.
  assert.equal(
    clientIpFromForwardedHeaders(
      new Headers({ 'x-forwarded-for': '6.6.6.6, 198.51.100.10, 130.211.0.5' }),
    ),
    '198.51.100.10',
  )
  // 정상 체인: "client, lb"
  assert.equal(
    clientIpFromForwardedHeaders(new Headers({ 'x-forwarded-for': '198.51.100.10, 130.211.0.5' })),
    '198.51.100.10',
  )
})

test('플랫폼 헤더(x-vercel-forwarded-for / x-real-ip)가 XFF보다 우선한다', () => {
  assert.equal(
    clientIpFromForwardedHeaders(
      new Headers({ 'x-vercel-forwarded-for': '203.0.113.2', 'x-forwarded-for': '6.6.6.6, 1.1.1.1' }),
    ),
    '203.0.113.2',
  )
  assert.equal(
    clientIpFromForwardedHeaders(new Headers({ 'x-real-ip': '203.0.113.3' })),
    '203.0.113.3',
  )
})

test('dev 단일 항목은 그대로, 유효하지 않은 값은 null', () => {
  assert.equal(clientIpFromForwardedHeaders(new Headers({ 'x-forwarded-for': '127.0.0.1' })), '127.0.0.1')
  assert.equal(clientIpFromForwardedHeaders(new Headers({ 'x-forwarded-for': 'junk' })), null)
  assert.equal(clientIpFromForwardedHeaders(new Headers()), null)
})
