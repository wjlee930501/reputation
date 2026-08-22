import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const onboardingPage = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)
const wikiPage = readFileSync(
  new URL('../app/hospitals/[id]/wiki/page.tsx', import.meta.url),
  'utf8',
)

test('photo uploads carry the rights evidence the public surface requires', () => {
  for (const field of [
    'photo_source_owner',
    'photo_rights_basis',
    'photo_evidence_reference',
  ]) {
    assert.match(onboardingPage, new RegExp(`fd\\.append\\('${field}'`))
  }
  // 서버가 허용하는 두 값 외에는 고를 수 없어야 저장 단계에서 막히지 않는다.
  const options = onboardingPage.match(
    /const PHOTO_RIGHTS_BASIS_OPTIONS = \[([\s\S]*?)\]/,
  )?.[1]
  assert.ok(options)
  assert.match(options, /'LICENSE'/)
  assert.match(options, /'OWNER_CONSENT'/)
})

test('a photo without rights evidence cannot be switched public by mistake', () => {
  assert.match(
    onboardingPage,
    /disabled=\{\s*pendingPublicId === s\.id \|\|\s*\(!s\.is_public && !photoProvenanceIsComplete\(s\)\)/,
  )
  assert.match(wikiPage, /!p\.is_public && !\(p\.photo_provenance\?\.is_complete \?\? false\)/)
})

test('the operator can record rights evidence and publish in one request', () => {
  assert.match(
    onboardingPage,
    /is_public: true,\s*photo_source_owner: evidence\.owner\.trim\(\),\s*photo_rights_basis: evidence\.basis,\s*photo_evidence_reference: evidence\.reference\.trim\(\)/,
  )
  // 0052로 비공개가 된 사진에 무엇이 비었는지 그대로 보여 준다.
  assert.match(onboardingPage, /provenance\?\.missing_message/)
})
