import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const appRoot = join(process.cwd(), 'app')
const globals = readFileSync(join(appRoot, 'globals.css'), 'utf8')
const hospitalSources = [
  readFileSync(join(appRoot, 'hospitals/[id]/layout.tsx'), 'utf8'),
  readFileSync(join(appRoot, 'hospitals/[id]/dashboard/page.tsx'), 'utf8'),
]

test('every hospital console color token is globally defined', () => {
  const used = new Set(
    hospitalSources.flatMap((source) =>
      [...source.matchAll(/var\((--color-revisit-[a-z0-9-]+)/g)].map((match) => match[1]),
    ),
  )
  const defined = new Set(
    [...globals.matchAll(/(--color-revisit-[a-z0-9-]+)\s*:/g)].map((match) => match[1]),
  )

  assert.deepEqual([...used].filter((token) => !defined.has(token)), [])
})
