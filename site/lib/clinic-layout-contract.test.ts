import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (file: string) => readFileSync(new URL(file, import.meta.url), 'utf8')
const home = read('../app/[slug]/page.tsx')
const hero = read('../app/[slug]/_components/ClinicHero.tsx')

test('home jump links only advertise sections backed by available data', () => {
  assert.match(home, /hospital\.treatments\.length > 0 && <a href="#treatments"/)
  assert.match(home, /physicians\.length > 0 && <a href="#doctor"/)
  assert.match(home, /contents\.length > 0 && <a href="#contents"/)
})

test('treatment and contact IDs are owned by their sections, never duplicated by wrappers', () => {
  assert.doesNotMatch(home, /<div id="(?:treatments|contact)"/)
  assert.match(read('../app/[slug]/_components/TreatmentGrid.tsx'), /<section id="treatments"/)
  assert.match(read('../app/[slug]/_components/ContactCard.tsx'), /<section id="contact"/)
})

test('subpage descriptions use the body role instead of inline 14px exceptions', () => {
  for (const page of ['visit/page.tsx', 'treatments/page.tsx', 'treatments/[treatmentSlug]/page.tsx']) {
    assert.doesNotMatch(read('../app/[slug]/' + page), /maxWidth: 720, fontSize: 14/)
  }
})

test('responsive hero media sizes include the shared mobile gutter', () => {
  assert.match(hero, /calc\(100vw - 40px\)/)
  assert.match(hero, /calc\(100vw - 64px\)/)
  assert.match(hero, /600px"/)
})

test('rendered layout gate rejects empty fixtures and non-loopback targets', () => {
  const gate = read('../../scripts/verify_clinic_layout.mjs')
  assert.match(gate, /Only a local preview is allowed/)
  assert.match(gate, /Empty fixtures cannot produce a green gate/)
  assert.match(gate, /Page HTTP status/)
  assert.match(gate, /Exactly one H1 is required/)
  assert.match(gate, /Horizontal document overflow/)
  assert.match(gate, /if \(summary\.failed\) process\.exitCode = 1/)
})
