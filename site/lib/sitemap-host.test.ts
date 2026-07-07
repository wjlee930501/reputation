import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveSitemapScope } from './sitemap-host.ts'

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

function withPlatform(siteUrl: string, fn: () => void): void {
  const originalSite = process.env.NEXT_PUBLIC_SITE_URL
  const originalNodeEnv = process.env.NODE_ENV
  try {
    setEnv('NODE_ENV', 'test')
    setEnv('NEXT_PUBLIC_SITE_URL', siteUrl)
    fn()
  } finally {
    setEnv('NEXT_PUBLIC_SITE_URL', originalSite)
    setEnv('NODE_ENV', originalNodeEnv)
  }
}

test('resolveSitemapScope returns "all" for the platform host', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.deepEqual(resolveSitemapScope('reputation.motionlabs.kr'), { kind: 'all' })
  })
})

test('resolveSitemapScope returns "all" when host is missing', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.deepEqual(resolveSitemapScope(null), { kind: 'all' })
    assert.deepEqual(resolveSitemapScope(''), { kind: 'all' })
  })
})

test('resolveSitemapScope returns "all" for local dev and run.app/vercel.app preview hosts', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.deepEqual(resolveSitemapScope('localhost:3000'), { kind: 'all' })
    assert.deepEqual(resolveSitemapScope('site-abc123.run.app'), { kind: 'all' })
    assert.deepEqual(resolveSitemapScope('reputation.vercel.app'), { kind: 'all' })
  })
})

test('resolveSitemapScope scopes a custom domain to just that host', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.deepEqual(resolveSitemapScope('clinic.example.com'), {
      kind: 'host',
      hostname: 'clinic.example.com',
    })
  })
})

test('resolveSitemapScope scopes a hybrid {slug}.{platform host} subdomain to that host', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.deepEqual(resolveSitemapScope('jang-clinic.reputation.motionlabs.kr'), {
      kind: 'host',
      hostname: 'jang-clinic.reputation.motionlabs.kr',
    })
  })
})

test('resolveSitemapScope normalizes case and strips the port from a custom host', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.deepEqual(resolveSitemapScope('Clinic.Example.COM:8443'), {
      kind: 'host',
      hostname: 'clinic.example.com',
    })
  })
})
