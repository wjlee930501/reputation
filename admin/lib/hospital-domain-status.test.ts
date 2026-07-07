import assert from 'node:assert/strict'
import test from 'node:test'

import { domainSearchText, readHospitalDomainStatus } from './hospital-domain-status.ts'

const ORIGINAL_SITE_URL = process.env.NEXT_PUBLIC_SITE_URL

function restoreSiteUrl(value: string | undefined): void {
  if (value === undefined) {
    Reflect.deleteProperty(process.env, 'NEXT_PUBLIC_SITE_URL')
    return
  }
  process.env.NEXT_PUBLIC_SITE_URL = value
}

function withSiteUrl<T>(value: string | undefined, run: () => T): T {
  const previous = process.env.NEXT_PUBLIC_SITE_URL
  restoreSiteUrl(value)
  try {
    return run()
  } finally {
    restoreSiteUrl(previous)
  }
}

test.after(() => {
  restoreSiteUrl(ORIGINAL_SITE_URL)
})

test('readHospitalDomainStatus separates live, DNS waiting, default, and unset states', () => {
  assert.deepEqual(
    readHospitalDomainStatus({ slug: 'clinic-a', aeo_domain: 'clinic-a.example.com', site_live: true }),
    {
      label: '운영중',
      detail: 'clinic-a.example.com',
      tone: 'live',
    },
  )
  assert.deepEqual(
    readHospitalDomainStatus({ slug: 'clinic-b', aeo_domain: 'clinic-b.example.com', site_live: false }),
    {
      label: 'DNS 대기',
      detail: 'clinic-b.example.com',
      tone: 'waiting',
    },
  )
  withSiteUrl(
    undefined,
    () => assert.deepEqual(
      readHospitalDomainStatus({ slug: 'clinic-c', site_built: true, site_live: false }),
      {
        label: '기본 주소',
        detail: 'clinic-c.reputation.motionlabs.kr',
        tone: 'default',
      },
    ),
  )
  assert.deepEqual(
    readHospitalDomainStatus({ slug: 'clinic-d', site_built: false, site_live: false }),
    {
      label: '미설정',
      detail: '프로파일에서 도메인 연결',
      tone: 'empty',
    },
  )
})

test('domainSearchText includes custom domain and derived status label', () => {
  const text = domainSearchText({
    name: '장편한외과의원',
    slug: 'jangclinic',
    aeo_domain: 'jangclinic.kr',
    site_live: false,
  })

  assert.match(text, /장편한외과의원/)
  assert.match(text, /jangclinic/)
  assert.match(text, /jangclinic\.kr/)
  assert.match(text, /dns 대기/i)
})

test('readHospitalDomainStatus derives the default host from NEXT_PUBLIC_SITE_URL', () => {
  withSiteUrl(
    'https://preview.reputation.example.test/some/path',
    () => assert.deepEqual(
      readHospitalDomainStatus({ slug: 'clinic-c', site_built: true, site_live: false }),
      {
        label: '기본 주소',
        detail: 'clinic-c.preview.reputation.example.test',
        tone: 'default',
      },
    ),
  )
})
