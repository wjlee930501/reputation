import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const onboardingPage = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)

test('photo uploads expose an explicit opt-in and only send true after it is checked', () => {
  assert.match(onboardingPage, /isPhotoSourceType\(type\) && \([\s\S]*공개 사이트에 표시/)
  assert.match(
    onboardingPage,
    /if \(isPhotoSourceType\(type\) && isPublic\) \{\s*fd\.append\('is_public', 'true'\)/,
  )
  assert.match(onboardingPage, /const \[isPublic, setIsPublic\] = useState\(false\)/)
  assert.match(onboardingPage, /setType\(e\.target\.value\)\s*setIsPublic\(false\)/)
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
    /isPhotoSourceType\(s\.source_type\) && \([\s\S]*s\.is_public \? '공개' : '비공개'/,
  )
  assert.match(
    onboardingPage,
    /`\/admin\/hospitals\/\$\{hospitalId\}\/essence\/sources\/\$\{sourceId\}\/public`[\s\S]*method: 'PATCH'[\s\S]*JSON\.stringify\(\{ is_public: next \}\)/,
  )
  assert.doesNotMatch(onboardingPage, /일괄 공개|전체 공개|publish.?all/i)
})
