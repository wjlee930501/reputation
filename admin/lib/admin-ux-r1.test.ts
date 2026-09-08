import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const newHospitalPage = readFileSync(
  new URL('../app/hospitals/new/page.tsx', import.meta.url),
  'utf8',
)
const photosSection = readFileSync(
  new URL('../app/hospitals/[id]/info/PhotosSection.tsx', import.meta.url),
  'utf8',
)

test('the suggested contract reference stays editable on the registration screen', () => {
  assert.match(newHospitalPage, /useState\(\(\) => suggestContractReference\(\)\)/)
  assert.match(newHospitalPage, /onChange=\{\(e\) => setContractReference\(e\.target\.value\)\}/)
})

test('photo guidance describes automatic publication and current state, not a required toggle', () => {
  assert.doesNotMatch(photosSection, /사진 자산은 토글로/)
  assert.match(photosSection, /PHOTO_PUBLIC_GATE_COPY/)
})
