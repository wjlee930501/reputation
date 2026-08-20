import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const NEW_HOSPITAL_PAGE = readFileSync(
  new URL('../app/hospitals/new/page.tsx', import.meta.url),
  'utf8',
)
const ONBOARDING_PAGE = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)
const ADMIN_SHELL = readFileSync(
  new URL('../app/AdminShell.tsx', import.meta.url),
  'utf8',
)

test('onboarding surfaces replace SLA jargon with an actionable Korean due date', () => {
  const surfaces = `${NEW_HOSPITAL_PAGE}\n${ONBOARDING_PAGE}`

  assert.doesNotMatch(surfaces, /\bSLA\b/)
  assert.match(NEW_HOSPITAL_PAGE, /인수 처리 기한/)
  assert.match(ONBOARDING_PAGE, /계약 정보를 확인하고 인수를 승인/)
  assert.match(ONBOARDING_PAGE, /병원명과 .* 개발팀에 전달/)
})

test('new hospital copy matches navigation and explains create plus acceptance', () => {
  assert.match(NEW_HOSPITAL_PAGE, /<h1[^>]*>신규 병원 온보딩<\/h1>/)
  assert.match(ADMIN_SHELL, /label: '신규 병원 온보딩'/)
  assert.match(NEW_HOSPITAL_PAGE, /병원을 생성하고 고객 인수를 승인한 뒤 온보딩 화면으로 이동합니다\./)
  assert.doesNotMatch(
    ADMIN_SHELL,
    /병원 자료, 운영 기준 자동 준비, 콘텐츠 자동 발행·공개 내용 확인, 월간 리포트 순서/,
  )
})
