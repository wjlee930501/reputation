import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const onboardingPage = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)

test('photo uploads send public in the same request without an extra opt-in', () => {
  assert.match(
    onboardingPage,
    /if \(isPhotoSourceType\(type\)\) \{[\s\S]{0,400}fd\.append\('is_public', 'true'\)/,
  )
  assert.doesNotMatch(onboardingPage, /const \[isPublic, setIsPublic\] = useState/)
  assert.doesNotMatch(onboardingPage, /공개 사이트에 표시/)
})

test('photo file input shows the locked multi-select hint above it', () => {
  const hintIndex = onboardingPage.indexOf('여러 장을 한 번에 고를 수 있습니다')
  const inputIndex = onboardingPage.indexOf('id="upload-file"')

  assert.ok(hintIndex >= 0)
  assert.ok(hintIndex < inputIndex)
  assert.match(onboardingPage, /\{isPhotoType && <p[^>]*>여러 장을 한 번에 고를 수 있습니다<\/p>\}/)
})

test('only photo rows expose their visibility badge and PATCH toggle', () => {
  const photoTypeGate = onboardingPage.match(
    /const PHOTO_SOURCE_TYPES = new Set\(\[([\s\S]*?)\]\)/,
  )?.[1]

  assert.ok(photoTypeGate)
  for (const sourceType of [
    'PHOTO_DOCTOR',
    'PHOTO_CLINIC_EXTERIOR',
    'PHOTO_CLINIC_INTERIOR',
    'PHOTO_TREATMENT_ROOM',
  ]) {
    assert.match(photoTypeGate, new RegExp(`'${sourceType}'`))
  }
  assert.doesNotMatch(photoTypeGate, /HOMEPAGE|NAVER_BLOG|INTERVIEW|BROCHURE|INTERNAL_NOTE/)
  assert.match(
    onboardingPage,
    /isPhotoSourceType\(s\.source_type\) && \([\s\S]*\{photoGate\?\.badge\}/,
  )
  assert.match(
    onboardingPage,
    /`\/admin\/hospitals\/\$\{hospitalId\}\/essence\/sources\/\$\{sourceId\}\/public`[\s\S]*method: 'PATCH'[\s\S]*JSON\.stringify\(\{ is_public: next \}\)/,
  )
  assert.doesNotMatch(onboardingPage, /일괄 공개|전체 공개|publish.?all/i)
})
