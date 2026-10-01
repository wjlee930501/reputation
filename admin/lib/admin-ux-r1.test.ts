import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const photosSection = readFileSync(
  new URL('../app/hospitals/[id]/info/PhotosSection.tsx', import.meta.url),
  'utf8',
)

test('photo guidance describes automatic publication and current state, not a required toggle', () => {
  assert.doesNotMatch(photosSection, /사진 자산은 토글로/)
  assert.match(photosSection, /PHOTO_PUBLIC_GATE_COPY/)
})
