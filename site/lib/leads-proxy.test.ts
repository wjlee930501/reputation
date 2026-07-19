import assert from 'node:assert/strict'
import test from 'node:test'

import { buildLeadOutboundHeaders, isLeadValidationUpstreamStatus } from './leads-proxy.ts'

const ORIGINAL_BFF_SECRET = process.env.SITE_BFF_SECRET

test.afterEach(() => {
  if (ORIGINAL_BFF_SECRET === undefined) {
    Reflect.deleteProperty(process.env, 'SITE_BFF_SECRET')
  } else {
    process.env.SITE_BFF_SECRET = ORIGINAL_BFF_SECRET
  }
})

test('lead proxy sends BFF auth even when visitor IP cannot be parsed', () => {
  process.env.SITE_BFF_SECRET = 'site-bff-secret'

  const headers = buildLeadOutboundHeaders(new Headers())

  assert.equal(headers['X-BFF-Auth'], 'site-bff-secret')
  assert.equal(headers['X-Visitor-IP'], undefined)
})

test('lead proxy forwards visitor IP when trusted proxy headers are available', () => {
  process.env.SITE_BFF_SECRET = 'site-bff-secret'

  const headers = buildLeadOutboundHeaders(
    new Headers({ 'x-forwarded-for': '198.51.100.7, 203.0.113.10' }),
  )

  assert.equal(headers['X-BFF-Auth'], 'site-bff-secret')
  assert.equal(headers['X-Visitor-IP'], '198.51.100.7')
  assert.equal(headers['X-Forwarded-For'], '198.51.100.7')
})

test('lead proxy treats upstream auth failures as service errors, not input validation', () => {
  assert.equal(isLeadValidationUpstreamStatus(400), true)
  assert.equal(isLeadValidationUpstreamStatus(422), true)
  assert.equal(isLeadValidationUpstreamStatus(401), false)
  assert.equal(isLeadValidationUpstreamStatus(403), false)
  assert.equal(isLeadValidationUpstreamStatus(429), false)
})
