import assert from 'node:assert/strict'
import test from 'node:test'

import {
  certificateIssuingCanBeRetried,
  certificateIssuingElapsedMinutes,
  customDomainPanelStatus,
  domainHeaderIsLive,
  domainHeaderStatus,
  domainLastCheckedLabel,
  domainSearchText,
  readHospitalDomainStatus,
} from './hospital-domain-status.ts'

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

test('a certificate job that just started still blocks a duplicate verify', () => {
  const now = Date.parse('2026-08-22T10:00:00Z')
  const startedAt = '2026-08-22T09:45:00Z'

  assert.equal(certificateIssuingElapsedMinutes(startedAt, now), 15)
  assert.equal(certificateIssuingCanBeRetried(startedAt, now), false)
})

test('a certificate job past the server lease can be verified again', () => {
  // 커밋 직후 워커가 죽으면 아무도 폴링하지 않는 ISSUING이 남는다. 버튼이 계속
  // 잠겨 있으면 운영자가 도메인을 되살릴 방법이 없다.
  const now = Date.parse('2026-08-22T10:00:00Z')
  const startedAt = '2026-08-22T09:20:00Z'

  assert.equal(certificateIssuingElapsedMinutes(startedAt, now), 40)
  assert.equal(certificateIssuingCanBeRetried(startedAt, now), true)
})

test('a certificate job with no start time never locks the button', () => {
  assert.equal(certificateIssuingElapsedMinutes(null), null)
  assert.equal(certificateIssuingCanBeRetried(null), true)
  assert.equal(certificateIssuingCanBeRetried('not-a-date'), true)
})

// ── A-1: 살아 있는 커스텀 도메인이 '확인 대기'로 남지 않는다 ──────────────────

test('a domain whose last live check answered is 운영 중 on the list, header and panel', () => {
  // 노원탑365의 실제 상태 — HTTPS 200 + CNAME 정상인데 도메인 재저장으로
  // domain_cert_* 가 비워져 세 화면이 모두 '미확인'을 말하고 있었다.
  const hospital = {
    slug: 'no1top365',
    aeo_domain: 'ai.no1top365.co.kr',
    site_live: true,
    domain_cert_dns_verified_at: null,
    domain_cert_job_state: null,
    domain_last_checked_at: '2026-08-22T03:00:00Z',
    domain_last_check_ok: true,
  }

  const status = readHospitalDomainStatus(hospital)
  assert.equal(status.label, '운영 중')
  assert.equal(status.tone, 'live')
  assert.match(status.detail, /^ai\.no1top365\.co\.kr · 마지막 확인 .+ · 응답 정상$/)

  assert.equal(domainHeaderStatus(hospital), '운영 중')
  assert.equal(domainHeaderIsLive(hospital), true)

  assert.equal(
    customDomainPanelStatus({
      hasUnsavedChange: false,
      domainSaved: true,
      activationReady: true,
      domain_cert_job_state: hospital.domain_cert_job_state,
      domain_cert_dns_verified_at: hospital.domain_cert_dns_verified_at,
      domain_last_check_ok: hospital.domain_last_check_ok,
    }),
    'live',
  )
})

test('a failed live check does not turn a waiting domain into 운영 중', () => {
  const hospital = {
    slug: 'clinic',
    aeo_domain: 'clinic.example.com',
    site_live: true,
    domain_last_checked_at: '2026-08-22T03:00:00Z',
    domain_last_check_ok: false,
  }

  const status = readHospitalDomainStatus(hospital)
  assert.equal(status.label, '공개 주소 확인 대기')
  assert.match(status.detail, /응답 실패$/)
  assert.equal(domainHeaderStatus(hospital), '저장됨 · DNS 미확인')
  assert.equal(domainHeaderIsLive(hospital), false)
})

test('a domain that was never checked reads exactly as before', () => {
  const status = readHospitalDomainStatus({
    slug: 'clinic',
    aeo_domain: 'clinic.example.com',
    site_live: true,
  })

  assert.equal(status.detail, 'clinic.example.com')
  assert.equal(status.label, '공개 주소 확인 대기')
})

test('domainLastCheckedLabel reports the outcome and ignores unusable timestamps', () => {
  assert.equal(domainLastCheckedLabel(null), null)
  assert.equal(domainLastCheckedLabel('not-a-date', true), null)
  assert.match(domainLastCheckedLabel('2026-08-22T03:00:00Z', true) ?? '', /응답 정상$/)
  assert.match(domainLastCheckedLabel('2026-08-22T03:00:00Z', false) ?? '', /응답 실패$/)
  assert.match(domainLastCheckedLabel('2026-08-22T03:00:00Z') ?? '', /^마지막 확인 /)
})

test('customDomainPanelStatus keeps the unsaved and empty branches ahead of live checks', () => {
  assert.equal(
    customDomainPanelStatus({
      hasUnsavedChange: true,
      domainSaved: true,
      activationReady: true,
      domain_last_check_ok: true,
    }),
    'unsaved',
  )
  assert.equal(
    customDomainPanelStatus({ hasUnsavedChange: false, domainSaved: false, activationReady: true }),
    'ready',
  )
  assert.equal(
    customDomainPanelStatus({ hasUnsavedChange: false, domainSaved: false, activationReady: false }),
    'empty',
  )
  assert.equal(
    customDomainPanelStatus({
      hasUnsavedChange: false,
      domainSaved: true,
      activationReady: true,
      domain_cert_job_state: 'ISSUING',
    }),
    'issuing',
  )
})
