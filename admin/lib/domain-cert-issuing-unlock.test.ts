// 인증서 발급이 만료 시간을 넘기면 재확인 버튼이 저절로 풀려야 한다.
//
// cert-status 폴링은 상태가 바뀔 때만 프로파일을 갱신하므로, ISSUING이 계속되는
// 동안에는 화면이 다시 그려지지 않는다. 서버가 클레임을 만료로 보는 시점(30분)이
// 지나도 버튼이 잠긴 채 남으면, 운영자는 새로고침을 해야만 도메인을 되살릴 수 있다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  CERTIFICATE_LEASE_MINUTES,
  certificateIssuingCanBeRetried,
} from './hospital-domain-status.ts'

const panel = readFileSync(
  new URL('../app/hospitals/[id]/DomainSetupPanel.tsx', import.meta.url),
  'utf8',
)

test('the panel keeps a ticking clock while a certificate is issuing', () => {
  assert.match(panel, /const \[now, setNow\] = useState\(\(\) => Date\.now\(\)\)/)
  const effect = panel.slice(panel.indexOf("if (profile.domain_cert_job_state !== 'ISSUING') return"))
  assert.match(effect, /setInterval\(\(\) => setNow\(Date\.now\(\)\), 30000\)/)
  assert.match(effect, /clearInterval\(tick\)/)
})

test('the lock and the elapsed label both read that clock, not a fixed render time', () => {
  assert.match(
    panel,
    /certificateIssuingCanBeRetried\(profile\.domain_cert_job_started_at, now\)/,
  )
  assert.match(
    panel,
    /_certElapsedMinutes\(profile\.domain_cert_job_started_at, now\)/,
  )
  // 버튼 두 곳 모두 같은 파생 상태를 쓴다 — 한쪽만 풀리는 일이 없어야 한다.
  const locked = panel.match(/disabled=\{domainVerifying[^}]*certificateIssuingLocked\}/g) ?? []
  assert.equal(locked.length, 2)
})

test('the clock crossing the server lease is what unlocks the button', () => {
  const startedAt = '2026-08-22T09:00:00Z'
  const justBefore = Date.parse(startedAt) + (CERTIFICATE_LEASE_MINUTES - 1) * 60_000
  const justAfter = Date.parse(startedAt) + CERTIFICATE_LEASE_MINUTES * 60_000

  assert.equal(certificateIssuingCanBeRetried(startedAt, justBefore), false)
  assert.equal(certificateIssuingCanBeRetried(startedAt, justAfter), true)
})
