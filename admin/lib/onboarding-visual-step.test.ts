// 공개 표면 시각 요소는 기존 온보딩 단계 안에서 승인한다.
//
// KEEP-8: 9번째 단계를 만들거나 새 admin 화면을 여는 대신, 기존 프로파일 단계
// 카드 안에 붙였다. 이 계약이 깨지면 온보딩 순서와 "N / 8" 표기가 함께 무너진다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { readdirSync } from 'node:fs'
import test from 'node:test'

const ONBOARDING_PAGE_URL = new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url)
const onboardingPage = readFileSync(ONBOARDING_PAGE_URL, 'utf8')
const lifecycle = readFileSync(new URL('./onboarding-lifecycle.ts', import.meta.url), 'utf8')
// 폼 자체는 병원 정보 화면과 공유한다. 온보딩이 갖는 것은 승인 체크리스트뿐이다.
const clinicVisualForm = readFileSync(
  new URL('../app/hospitals/[id]/info/ClinicVisualForm.tsx', import.meta.url),
  'utf8',
)

test('the visual form is rendered inside the existing profile step, not a new one', () => {
  const profileBody = onboardingPage.indexOf('function ProfileStepBody(')
  const visualForm = onboardingPage.indexOf('<ClinicVisualForm')

  assert.ok(profileBody >= 0, 'ProfileStepBody를 찾지 못했다')
  assert.ok(visualForm > profileBody, '시각 요소 폼이 프로파일 단계 안에 있지 않다')
  assert.match(onboardingPage, /step\.key === 'profile' && \(\s*<ProfileStepBody/)
})

test('no ninth onboarding step key was introduced', () => {
  const keyUnion = lifecycle.match(/export type OnboardingStepKey =([\s\S]*?)\n\n/)?.[1]
  assert.ok(keyUnion, 'OnboardingStepKey 정의를 찾지 못했다')

  const keys = [...keyUnion.matchAll(/'([a-z0-9_]+)'/g)].map((match) => match[1])
  assert.deepEqual(keys, [
    'handoff',
    'profile',
    'v0',
    'site',
    'processing',
    'philosophy_approved',
    'schedule',
    'live',
    'first_publish',
    'sov',
  ])
  for (const invented of ['visual', 'brand', 'theme', 'palette', 'design']) {
    assert.doesNotMatch(keyUnion, new RegExp(`'${invented}`), `${invented} 단계가 추가됐다`)
  }
})

test('the visual form saves through the existing profile PATCH', () => {
  assert.match(
    clinicVisualForm,
    /`\/admin\/hospitals\/\$\{hospitalId\}\/profile`[\s\S]{0,120}method: 'PATCH'/,
  )
  for (const field of [
    'brand_primary_color',
    'hero_headline',
    'hero_description',
    'site_access_mode',
  ]) {
    assert.match(clinicVisualForm, new RegExp(`${field}:`), `${field}을 저장하지 않는다`)
  }
  // logo_url은 업로드 엔드포인트가 소유한다 — 폼이 되돌려 보내면 자산 참조를 덮어쓴다.
  assert.doesNotMatch(clinicVisualForm, /logo_url:/)
})

test('only one brand color is asked for in the onboarding step', () => {
  const formStart = clinicVisualForm.indexOf('export function ClinicVisualForm(')

  assert.ok(formStart >= 0, 'ClinicVisualForm을 찾지 못했다')
  assert.doesNotMatch(clinicVisualForm, /brand_accent_color/)
  assert.match(clinicVisualForm, /brand_primary_color/)
  // 온보딩 페이지에는 폼 사본이 남아 있지 않다.
  assert.doesNotMatch(onboardingPage, /function ClinicVisualForm\(/)
})

test('photos are described as optional inside the visual step', () => {
  const checklistStart = onboardingPage.indexOf('function ClinicVisualChecklist(')
  const checklist = onboardingPage.slice(checklistStart)

  assert.ok(checklistStart >= 0, 'ClinicVisualChecklist를 찾지 못했다')
  assert.match(checklist, /실사진은 필수가 아니며/)
  assert.doesNotMatch(checklist, /사진.{0,6}필수입니다/)
})

test('no new admin surface was opened for visual configuration', () => {
  const hospitalRoutes = readdirSync(new URL('../app/hospitals/[id]/', import.meta.url), {
    withFileTypes: true,
  })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)

  // 시각 설정은 기존 profile/onboarding 화면이 소유한다.
  const visualRoutes = hospitalRoutes.filter((route) =>
    /visual|brand|theme|palette|design|logo/i.test(route),
  )

  assert.deepEqual(visualRoutes, [])
  assert.ok(hospitalRoutes.includes('profile'))
  assert.ok(hospitalRoutes.includes('onboarding'))
})
