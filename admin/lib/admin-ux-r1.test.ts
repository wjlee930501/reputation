import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const profilePage = readFileSync(
  new URL('../app/hospitals/[id]/profile/page.tsx', import.meta.url),
  'utf8',
)
const dashboardPage = readFileSync(
  new URL('../app/hospitals/[id]/dashboard/page.tsx', import.meta.url),
  'utf8',
)
const newHospitalPage = readFileSync(
  new URL('../app/hospitals/new/page.tsx', import.meta.url),
  'utf8',
)
const onboardingPage = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)
const essencePage = readFileSync(
  new URL('../app/hospitals/[id]/essence/page.tsx', import.meta.url),
  'utf8',
)
const hospitalLayout = readFileSync(
  new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
  'utf8',
)
const wikiPage = readFileSync(
  new URL('../app/hospitals/[id]/wiki/page.tsx', import.meta.url),
  'utf8',
)

test('profile R1 offers one-click weekdays and keeps coordinates under advanced controls', () => {
  assert.match(profilePage, /월–금 한 번에 채우기/)
  for (const day of ['mon', 'tue', 'wed', 'thu', 'fri']) {
    assert.match(profilePage, new RegExp(`${day}: weekdayCommonHours`))
  }
  const advanced = profilePage.indexOf('고급 · 위도/경도 직접 수정')
  const latitude = profilePage.indexOf('id="profile-latitude"')
  assert.ok(advanced >= 0 && advanced < latitude)
  assert.match(profilePage, /geocode_address: !coordinatesManuallyEdited/)
})

test('new onboarding contract reference is generated but remains editable', () => {
  assert.match(newHospitalPage, /defaultContractReference\(creationRequestId\)/)
  assert.match(newHospitalPage, /value=\{contractReference\} onChange=\{\(e\) => setContractReference/)
})

test('URL material title is fetched into an editable field and author defaults to the account', () => {
  assert.match(onboardingPage, /essence\/sources\/url-title/)
  assert.match(onboardingPage, /setTitle\(preview\.title\)/)
  assert.match(onboardingPage, /onChange=\{\(e\) => \{\s*setTitle\(e\.target\.value\)/)
  assert.match(essencePage, /setSourceCreatedBy\(\(prev\) => prev \|\| name \|\| ''\)/)
})

test('dashboard does not hard-code dead-end recovery button guidance', () => {
  assert.doesNotMatch(dashboardPage, /지금 발행 \(운영 복구\)/)
  assert.doesNotMatch(dashboardPage, /해당 버튼이 없으면/)
})

test('photo guidance describes automatic publication and current state, not a required Wiki toggle', () => {
  assert.doesNotMatch(hospitalLayout, /사진 공개 토글/)
  assert.match(hospitalLayout, /사진 권리·공개 상태/)
  assert.doesNotMatch(wikiPage, /사진 자산은 토글로/)
  assert.match(wikiPage, /사진 자산의 사용 권리 기록과 현재 병원 사이트 표시 상태/)
})
