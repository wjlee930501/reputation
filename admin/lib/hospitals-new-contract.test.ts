import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const PAGE = readFileSync(new URL('../app/hospitals/new/page.tsx', import.meta.url), 'utf8')
const LEADS = readFileSync(new URL('../app/leads/page.tsx', import.meta.url), 'utf8')

test('contract registration submits once to the single-transaction endpoint', () => {
  assert.equal(PAGE.match(/method: 'POST'/g)?.length, 1)
  assert.match(PAGE, /'\/admin\/hospitals\/register-contract'/)
  // 옛 3단계(생성 → 계약 기록 → 인수 수락)를 화면이 다시 이어 붙이지 않는다.
  assert.doesNotMatch(PAGE, /handoffs\/|\/convert|onboarding_request_id|sessionStorage/)
})

test('the screen asks for the six contract fields and nothing else', () => {
  for (const label of ['병원명', '계약 번호', '계약 효력일', '영업 담당']) {
    assert.ok(PAGE.includes(label), `missing field: ${label}`)
  }
  // 요금제·담당 AE는 통일 용어 상수를 그대로 읽는다.
  assert.match(PAGE, /\{ADMIN_COPY\.plan\}/)
  assert.match(PAGE, /\{ADMIN_COPY\.aeOwner\}/)
  assert.doesNotMatch(PAGE, /인수 처리 기한|온보딩|대신 승인하는 사유/)
  assert.match(PAGE, /<h1[^>]*>계약 등록<\/h1>/)
})

test('a duplicate hospital offers the existing hospital instead of a retry', () => {
  assert.match(PAGE, /existingHospitalId\(cause\.detail\)/)
  assert.match(PAGE, /기존 병원 열기/)
  assert.match(PAGE, /href=\{`\/hospitals\/\$\{existingHospital\}\/info`\}/)
  assert.doesNotMatch(PAGE, /다시 시도|다시 누르/)
})

test('success lands on the hospital info screen', () => {
  assert.match(PAGE, /router\.push\(`\/hospitals\/\$\{created\.id\}\/info`\)/)
  assert.doesNotMatch(PAGE, /\/onboarding/)
})

test('the lead list starts contract registration on the one-screen route', () => {
  assert.match(LEADS, /href=\{getOnboardingHref\(lead\)\}[\s\S]{0,300}계약 등록/)
  // 남은 '온보딩 시작' 문자열은 더 이상 열리지 않는 전환 모달 안에만 있다(PR-1E Task 2에서 삭제).
  assert.doesNotMatch(LEADS, />\s*온보딩 시작\s*</)
})
