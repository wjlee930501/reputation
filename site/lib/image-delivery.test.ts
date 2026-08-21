import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function componentSource(name: string): string {
  return readFileSync(new URL(`../app/[slug]/_components/${name}.tsx`, import.meta.url), 'utf8')
}

test('onboarding portraits use responsive Next image delivery instead of the original upload', () => {
  const source = componentSource('ClinicAvatar')

  assert.match(source, /import Image from 'next\/image'/)
  assert.match(source, /<Image/)
  assert.match(source, /\bfill\b/)
  assert.match(source, /\bsizes=/)
  assert.doesNotMatch(source, /<img\b/)
})

test('content covers use responsive Next image delivery instead of the original image', () => {
  const source = componentSource('ContentCover')

  assert.match(source, /import Image from 'next\/image'/)
  assert.match(source, /<Image/)
  assert.match(source, /\bfill\b/)
  assert.match(source, /\bsizes=/)
  assert.doesNotMatch(source, /<img\b/)
})
