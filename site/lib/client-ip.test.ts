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

test('XFF가 있으면 위조된 x-real-ip / x-vercel-forwarded-for를 무시한다 (스푸핑 방어)', () => {
  // GCLB는 인바운드 x-real-ip를 제거하지 않는다 — 공격자가 X-Real-IP를 임의 설정해도
  // LB가 붙인 XFF 체인이 항상 이겨야 rate-limit key·consent_ip를 조작할 수 없다.
  assert.equal(
    clientIpFromForwardedHeaders(
      new Headers({ 'x-real-ip': '6.6.6.6', 'x-forwarded-for': '198.51.100.10, 130.211.0.5' }),
    ),
    '198.51.100.10',
  )
  assert.equal(
    clientIpFromForwardedHeaders(
      new Headers({ 'x-vercel-forwarded-for': '6.6.6.6', 'x-forwarded-for': '198.51.100.10, 130.211.0.5' }),
    ),
    '198.51.100.10',
  )
  // XFF가 있는데 파싱이 불가능해도 위조 가능한 헤더로 내려가지 않는다.
  assert.equal(
    clientIpFromForwardedHeaders(new Headers({ 'x-real-ip': '6.6.6.6', 'x-forwarded-for': 'junk' })),
    null,
  )
})

test('x-real-ip / x-vercel-forwarded-for는 XFF가 아예 없을 때만 fallback으로 쓴다', () => {
  assert.equal(
    clientIpFromForwardedHeaders(new Headers({ 'x-real-ip': '203.0.113.3' })),
    '203.0.113.3',
  )
  assert.equal(
    clientIpFromForwardedHeaders(new Headers({ 'x-vercel-forwarded-for': '203.0.113.2' })),
    '203.0.113.2',
  )
})

test('dev 단일 항목은 그대로, 유효하지 않은 값은 null', () => {
  assert.equal(clientIpFromForwardedHeaders(new Headers({ 'x-forwarded-for': '127.0.0.1' })), '127.0.0.1')
  assert.equal(clientIpFromForwardedHeaders(new Headers({ 'x-forwarded-for': 'junk' })), null)
  assert.equal(clientIpFromForwardedHeaders(new Headers()), null)
})
