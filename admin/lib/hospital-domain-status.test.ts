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
  // DM-U3 new contract: cert DONE = 운영 중
  assert.deepEqual(
    readHospitalDomainStatus({ slug: 'clinic-a', aeo_domain: 'clinic-a.example.com', site_live: true, domain_cert_job_state: 'DONE' }),
    {
      label: '운영 중',
      detail: 'clinic-a.example.com',
      tone: 'live',
    },
  )
  // DNS not verified = 공개 주소 확인 대기
  assert.deepEqual(
    readHospitalDomainStatus({ slug: 'clinic-b', aeo_domain: 'clinic-b.example.com', site_live: false }),
    {
      label: '공개 주소 확인 대기',
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
      detail: '병원 기본 정보에서 공개 주소 연결',
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
  assert.match(text, /공개 주소 확인 대기/)
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

test('custom domain with DNS verified but cert not done shows dns_verified', () => {
  assert.deepEqual(
    readHospitalDomainStatus({
      slug: 'test',
      aeo_domain: 'clinic.example.com',
      site_live: true,
      domain_cert_dns_verified_at: '2026-08-20T10:00:00Z',
    }),
    {
      label: 'DNS 확인 완료',
      detail: 'clinic.example.com',
      tone: 'dns_verified',
    },
  )
})

test('custom domain with cert ISSUING shows issuing', () => {
  assert.deepEqual(
    readHospitalDomainStatus({
      slug: 'test',
      aeo_domain: 'clinic.example.com',
      site_live: true,
      domain_cert_dns_verified_at: '2026-08-20T10:00:00Z',
      domain_cert_job_state: 'ISSUING',
    }),
    {
      label: '인증서 발급 중',
      detail: 'clinic.example.com',
      tone: 'issuing',
    },
  )
})

test('custom domain with cert FAILED shows failed', () => {
  assert.deepEqual(
    readHospitalDomainStatus({
      slug: 'test',
      aeo_domain: 'clinic.example.com',
      site_live: true,
      domain_cert_dns_verified_at: '2026-08-20T10:00:00Z',
      domain_cert_job_state: 'FAILED',
    }),
    {
      label: '인증서 실패',
      detail: 'clinic.example.com',
      tone: 'failed',
    },
  )
})

test('site_live true + custom domain + DNS not verified must NOT be 운영 중', () => {
  const status = readHospitalDomainStatus({
    slug: 'test',
    aeo_domain: 'clinic.example.com',
    site_live: true,
    // domain_cert_dns_verified_at and domain_cert_job_state are both absent
  })
  assert.notEqual(status.label, '운영 중')
  assert.equal(status.label, '공개 주소 확인 대기')
  assert.equal(status.tone, 'waiting')
})

test('domain with uppercase is normalized to lowercase', () => {
  const status = readHospitalDomainStatus({
    slug: 'test',
    aeo_domain: 'CLINIC.EXAMPLE.COM',
    domain_cert_job_state: 'DONE',
  })
  assert.equal(status.detail, 'clinic.example.com')
})
