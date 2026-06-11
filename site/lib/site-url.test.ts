import assert from 'node:assert/strict'
import test from 'node:test'

import { canonicalBase, normalizeCustomDomain, platformSiteUrl } from './site-url.ts'

test('platformSiteUrl falls back to default and strips trailing slash', () => {
  const original = process.env.NEXT_PUBLIC_SITE_URL
  try {
    delete process.env.NEXT_PUBLIC_SITE_URL
    assert.equal(platformSiteUrl(), 'https://reputation.co.kr')
    process.env.NEXT_PUBLIC_SITE_URL = 'https://example.com/'
    assert.equal(platformSiteUrl(), 'https://example.com')
  } finally {
    if (original === undefined) delete process.env.NEXT_PUBLIC_SITE_URL
    else process.env.NEXT_PUBLIC_SITE_URL = original
  }
})

test('normalizeCustomDomain accepts hostnames and normalizes loose input', () => {
  assert.equal(normalizeCustomDomain('clinic.example.com'), 'clinic.example.com')
  assert.equal(normalizeCustomDomain('Clinic.Example.COM'), 'clinic.example.com')
  assert.equal(normalizeCustomDomain('https://clinic.example.com/'), 'clinic.example.com')
  assert.equal(normalizeCustomDomain('clinic.example.com:443'), 'clinic.example.com')
  assert.equal(normalizeCustomDomain('https://clinic.example.com/path?q=1'), 'clinic.example.com')
})

test('normalizeCustomDomain rejects invalid values', () => {
  assert.equal(normalizeCustomDomain(null), null)
  assert.equal(normalizeCustomDomain(undefined), null)
  assert.equal(normalizeCustomDomain(''), null)
  assert.equal(normalizeCustomDomain('   '), null)
  assert.equal(normalizeCustomDomain('no-dot-hostname'), null)
  assert.equal(normalizeCustomDomain('-bad.example.com'), null)
  assert.equal(normalizeCustomDomain('exa mple.com'), null)
  assert.equal(normalizeCustomDomain('javascript:alert(1)'), null)
})

test('canonicalBase uses https custom domain when aeo_domain is set', () => {
  assert.equal(
    canonicalBase({ aeo_domain: 'clinic.example.com' }),
    'https://clinic.example.com',
  )
  assert.equal(
    canonicalBase({ aeo_domain: 'https://clinic.example.com/' }),
    'https://clinic.example.com',
  )
})

test('canonicalBase falls back to platform URL when aeo_domain missing or invalid', () => {
  assert.equal(canonicalBase({ aeo_domain: null }), platformSiteUrl())
  assert.equal(canonicalBase({}), platformSiteUrl())
  assert.equal(canonicalBase(null), platformSiteUrl())
  assert.equal(canonicalBase({ aeo_domain: 'not a domain' }), platformSiteUrl())
})
