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

test('onboarding surfaces replace SLA jargon with an actionable Korean due date', () => {
  const surfaces = `${NEW_HOSPITAL_PAGE}\n${ONBOARDING_PAGE}`

  assert.doesNotMatch(surfaces, /\bSLA\b/)
  assert.match(NEW_HOSPITAL_PAGE, /인수 처리 기한/)
  assert.match(ONBOARDING_PAGE, /계약 정보를 확인하고 인수를 승인/)
  assert.match(ONBOARDING_PAGE, /병원명과 .* 개발팀에 전달/)
})
