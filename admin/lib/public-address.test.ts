import assert from 'node:assert/strict'
import test from 'node:test'

import { publicContentUrl, readPublicAddresses } from './public-address.ts'

const live = { kind: 'live' as const, remaining: [] }
const notLive = { kind: 'not_live' as const, remaining: [] }

test('a live hospital without its own domain opens on the platform address', () => {
  const addresses = readPublicAddresses({ slug: 'haengbok', public_service_state: live })
  assert.equal(addresses.live?.host, 'haengbok.reputation.motionlabs.kr')
  assert.equal(addresses.custom, null)
  assert.equal(publicContentUrl({ slug: 'haengbok', public_service_state: live }, 'c1'), 'https://haengbok.reputation.motionlabs.kr/contents/c1')
})

test('an issued custom domain becomes the live address', () => {
  const addresses = readPublicAddresses({
    slug: 'haengbok',
    aeo_domain: 'AI.Clinic.co.kr',
    public_service_state: live,
    domain_cert_job_state: 'DONE',
  })
  assert.equal(addresses.live?.url, 'https://ai.clinic.co.kr')
  assert.equal(addresses.platform?.open, true)
})

test('a saved but not yet working custom domain never becomes a link', () => {
  for (const pending of [
    { domain_cert_job_state: 'ISSUING' },
    { domain_cert_job_state: 'FAILED' },
    { domain_cert_job_state: null, domain_last_check_ok: false },
  ]) {
    const addresses = readPublicAddresses({ slug: 's', aeo_domain: 'ai.clinic.co.kr', public_service_state: live, ...pending })
    assert.equal(addresses.custom?.open, false, JSON.stringify(pending))
    assert.equal(addresses.live?.host, 's.reputation.motionlabs.kr', JSON.stringify(pending))
  }
})

test('a hospital that is not serving has no live address and no article link', () => {
  const hospital = { slug: 's', aeo_domain: 'ai.clinic.co.kr', public_service_state: notLive, domain_cert_job_state: 'DONE' }
  const addresses = readPublicAddresses(hospital)
  assert.equal(addresses.live, null)
  assert.equal(addresses.platform?.open, false)
  assert.equal(addresses.custom?.open, false)
  assert.equal(publicContentUrl(hospital, 'c1'), null)
})

import { readFileSync } from 'node:fs'

// 공개 링크는 한 함수에서만 — 콘텐츠 화면이 자기 도메인 저장값으로 링크를 만들던 문제(2026-11-20).
test('content screen and hospital header build public links from public-address only', () => {
  const content = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')
  const header = readFileSync(new URL('../app/hospitals/[id]/layout.tsx', import.meta.url), 'utf8')
  assert.match(content, /publicContentUrl/)
  assert.doesNotMatch(content, /platformSubdomainHost|buildPublicContentUrl/)
  assert.match(content, /isVisibleOnPublicPage/)
  assert.match(header, /readPublicAddresses/)
  assert.match(header, /AddressLink/)
})
