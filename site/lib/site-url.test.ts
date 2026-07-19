import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canonicalBase,
  canonicalHospitalUrl,
  normalizeCustomDomain,
  platformSiteUrl,
} from './site-url.ts'

function setEnv(name: 'NEXT_PUBLIC_SITE_URL' | 'NODE_ENV', value: string | undefined): void {
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

test('platformSiteUrl falls back to default and strips trailing slash', () => {
  const original = process.env.NEXT_PUBLIC_SITE_URL
  const originalNodeEnv = process.env.NODE_ENV
  try {
    setEnv('NODE_ENV', 'test')
    setEnv('NEXT_PUBLIC_SITE_URL', undefined)
    assert.equal(platformSiteUrl(), 'https://reputation.motionlabs.kr')
    setEnv('NEXT_PUBLIC_SITE_URL', 'https://example.com/')
    assert.equal(platformSiteUrl(), 'https://example.com')
  } finally {
    setEnv('NEXT_PUBLIC_SITE_URL', original)
    setEnv('NODE_ENV', originalNodeEnv)
  }
})

test('platformSiteUrl fails closed when production env is missing', () => {
  const original = process.env.NEXT_PUBLIC_SITE_URL
  const originalNodeEnv = process.env.NODE_ENV
  try {
    setEnv('NODE_ENV', 'production')
    setEnv('NEXT_PUBLIC_SITE_URL', undefined)
    assert.throws(() => platformSiteUrl(), /NEXT_PUBLIC_SITE_URL/)
  } finally {
    setEnv('NEXT_PUBLIC_SITE_URL', original)
    setEnv('NODE_ENV', originalNodeEnv)
  }
})

test('platformSiteUrl rejects unsafe production origins', () => {
  const original = process.env.NEXT_PUBLIC_SITE_URL
  const originalNodeEnv = process.env.NODE_ENV
  try {
    setEnv('NODE_ENV', 'production')

    setEnv('NEXT_PUBLIC_SITE_URL', 'http://localhost:3000')
    assert.throws(() => platformSiteUrl(), /https/)

    setEnv('NEXT_PUBLIC_SITE_URL', 'https://127.0.0.1')
    assert.throws(() => platformSiteUrl(), /public hostname/)

    setEnv('NEXT_PUBLIC_SITE_URL', 'https://[::1]')
    assert.throws(() => platformSiteUrl(), /public hostname/)

    setEnv('NEXT_PUBLIC_SITE_URL', 'https://admin.localhost')
    assert.throws(() => platformSiteUrl(), /public hostname/)
  } finally {
    setEnv('NEXT_PUBLIC_SITE_URL', original)
    setEnv('NODE_ENV', originalNodeEnv)
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

test('canonicalHospitalUrl hides the internal slug on a custom domain', () => {
  assert.equal(
    canonicalHospitalUrl({ aeo_domain: 'clinic.example.com' }, 'demo-clinic'),
    'https://clinic.example.com',
  )
  assert.equal(
    canonicalHospitalUrl({ aeo_domain: 'clinic.example.com' }, 'demo-clinic', '/contents/1'),
    'https://clinic.example.com/contents/1',
  )
})

test('canonicalHospitalUrl keeps the slug on the platform domain', () => {
  assert.equal(
    canonicalHospitalUrl({}, 'demo-clinic', 'doctor'),
    `${platformSiteUrl()}/demo-clinic/doctor`,
  )
})
