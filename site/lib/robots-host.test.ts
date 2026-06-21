import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveSitemapUrl } from './robots-host.ts'

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

test('resolveSitemapUrl keeps platform host pointing at platform sitemap', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.equal(
      resolveSitemapUrl('reputation.motionlabs.kr', 'https'),
      'https://reputation.motionlabs.kr/sitemap.xml',
    )
  })
})

test('resolveSitemapUrl falls back to platform sitemap when host is missing', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.equal(resolveSitemapUrl(null, null), 'https://reputation.motionlabs.kr/sitemap.xml')
    assert.equal(resolveSitemapUrl('', null), 'https://reputation.motionlabs.kr/sitemap.xml')
  })
})

test('resolveSitemapUrl keeps run.app/vercel.app preview hosts on platform sitemap', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.equal(
      resolveSitemapUrl('site-abc123.run.app', 'https'),
      'https://reputation.motionlabs.kr/sitemap.xml',
    )
  })
})

test('resolveSitemapUrl points custom domain at its own origin', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.equal(
      resolveSitemapUrl('clinic.example.com', 'https'),
      'https://clinic.example.com/sitemap.xml',
    )
  })
})

test('resolveSitemapUrl honours forwarded proto and trims comma-list', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.equal(
      resolveSitemapUrl('clinic.example.com', 'http'),
      'http://clinic.example.com/sitemap.xml',
    )
    // x-forwarded-proto가 누적된 경우 첫 토큰만 사용한다.
    assert.equal(
      resolveSitemapUrl('clinic.example.com', 'https,http'),
      'https://clinic.example.com/sitemap.xml',
    )
    // proto 미상이면 https로 안전 기본값.
    assert.equal(
      resolveSitemapUrl('clinic.example.com', null),
      'https://clinic.example.com/sitemap.xml',
    )
  })
})

test('resolveSitemapUrl preserves a port in the custom host origin', () => {
  withPlatform('https://reputation.motionlabs.kr', () => {
    assert.equal(
      resolveSitemapUrl('clinic.example.com:8443', 'https'),
      'https://clinic.example.com:8443/sitemap.xml',
    )
  })
})
