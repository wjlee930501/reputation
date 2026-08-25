import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('clinic-local 404 offers known clinic contact routes and preserves the global fallback', () => {
  const local = readFileSync(new URL('../app/[slug]/ClinicNotFound.tsx', import.meta.url), 'utf8')
  const wrapper = readFileSync(new URL('../app/[slug]/not-found.tsx', import.meta.url), 'utf8')

  assert.match(wrapper, /<ClinicNotFound/)
  assert.match(local, />병원 홈</)
  assert.match(local, /href=\{`tel:\$\{hospital\.phone\}`\}/)
  assert.match(local, />\s*전체 진료시간\s*</)
  assert.match(local, /mapsUrl &&/)
  assert.match(local, /Re:putation 홈으로 이동/)
})

test('skip-link destinations exist on diagnosis, status, error, and not-found pages', () => {
  const pages = [
    '../app/ai-diagnosis/page.tsx',
    '../app/ai-diagnosis/status/[token]/page.tsx',
    '../app/error.tsx',
    '../app/not-found.tsx',
  ]

  for (const page of pages) {
    const source = readFileSync(new URL(page, import.meta.url), 'utf8')
    assert.match(source, /<main[^>]*id="main-content"/, page)
  }
})
