import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  describeDomainArtifacts,
  describeScheduleArtifacts,
  selectV0ReportArtifacts,
} from './onboarding-artifacts.ts'

test('the initial diagnosis step links the PDF the operator has to report with', () => {
  const artifacts = selectV0ReportArtifacts([
    { id: 'a', report_type: 'MONTHLY', has_pdf: true, download_url: '/monthly' },
    { id: 'b', report_type: 'V0', has_pdf: true, download_url: '/v0', created_at: '2026-07-02T00:00:00Z' },
  ])

  assert.equal(artifacts.length, 1)
  assert.equal(artifacts[0].href, '/v0')
  assert.match(artifacts[0].value, /2026/)
})

test('a monthly PDF never stands in for the initial diagnosis artifact', () => {
  const artifacts = selectV0ReportArtifacts([
    { id: 'a', report_type: 'MONTHLY', has_pdf: true, download_url: '/monthly' },
  ])

  assert.equal(artifacts[0].missing, true)
  assert.equal(artifacts[0].href, undefined)
  assert.match(artifacts[0].value, /아직 생성되지 않았습니다/)
})

test('an initial diagnosis row without a PDF says the report has to be remade', () => {
  const artifacts = selectV0ReportArtifacts([{ id: 'b', report_type: 'V0', has_pdf: false }])

  assert.equal(artifacts[0].missing, true)
  assert.match(artifacts[0].value, /PDF 없음/)
})

test('the domain step shows the live address, the last check and the operating state', () => {
  const artifacts = describeDomainArtifacts({
    aeo_domain: 'ai.example.co.kr',
    slug: 'example',
    site_live: true,
    domain_last_checked_at: '2026-08-20T02:00:00Z',
    domain_last_check_ok: true,
  })

  assert.equal(artifacts[0].value, 'ai.example.co.kr')
  assert.equal(artifacts[0].href, 'https://ai.example.co.kr')
  assert.match(artifacts[1].value, /정상/)
  assert.equal(artifacts[2].value, '운영 중')
})

test('a failed domain check surfaces its own reason instead of a generic warning', () => {
  const artifacts = describeDomainArtifacts({
    aeo_domain: 'ai.example.co.kr',
    site_live: false,
    domain_last_checked_at: '2026-08-20T02:00:00Z',
    domain_last_check_ok: false,
    domain_last_check_reason: 'CNAME이 다른 대상을 가리킵니다',
  })

  const check = artifacts.find((artifact) => artifact.label === '연결 확인')
  assert.equal(check?.missing, true)
  assert.match(check?.value ?? '', /CNAME이 다른 대상을 가리킵니다/)
})

test('a hospital without its own domain is not reported as broken', () => {
  const artifacts = describeDomainArtifacts({ slug: 'example', site_live: true })

  assert.equal(artifacts[0].missing, undefined)
  assert.match(artifacts[0].value, /기본 플랫폼 주소/)
  // 자기 도메인이 없으면 연결 확인 줄을 만들지 않는다 — 확인할 대상이 없다.
  assert.equal(artifacts.some((artifact) => artifact.label === '연결 확인'), false)
})

test('the schedule step names the plan and the publishing weekdays', () => {
  const artifacts = describeScheduleArtifacts({
    plan: 'PLAN_16',
    publish_days: [1, 3],
    active_from: '2026-09-01',
  })

  assert.equal(artifacts[0].value, '그로워 16편/월')
  assert.equal(artifacts[1].value, '화·목')
  assert.equal(artifacts[2].label, '적용 시작')
})

test('a missing schedule says so instead of showing an empty weekday list', () => {
  const artifacts = describeScheduleArtifacts(null)

  assert.equal(artifacts.length, 1)
  assert.equal(artifacts[0].missing, true)
})

test('an unknown plan code is shown as-is rather than dropped', () => {
  const artifacts = describeScheduleArtifacts({ plan: 'PLAN_99', publish_days: [] })

  assert.equal(artifacts[0].value, 'PLAN_99')
  assert.equal(artifacts[1].missing, true)
})

test('the onboarding accordion renders these artifacts for the operational steps', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /selectV0ReportArtifacts/)
  assert.match(page, /describeDomainArtifacts/)
  assert.match(page, /describeScheduleArtifacts/)
})
