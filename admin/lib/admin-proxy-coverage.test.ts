import assert from 'node:assert/strict'
import test from 'node:test'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { ALLOWED_PREFIXES } from './admin-api-proxy-route.ts'

/**
 * 화면이 부르는 admin 경로는 전부 프록시 allowlist에 있어야 한다.
 *
 * 프록시는 첫 경로 조각이 `ALLOWED_PREFIXES`에 없으면 **백엔드에 라우터가 있어도**
 * 403 Forbidden으로 끊는다. 이건 가정이 아니라 실제로 난 사고다 — `/admin/lead-diagnoses`
 * 엔드포인트를 만들고 화면까지 붙여 배포했는데, 목록에 접두사를 더하지 않아 운영에서
 * 첫 제출이 403으로 떨어졌다.
 *
 * 그 사고가 조용했던 이유는 단위 테스트와 타입 검사 어디에도 "화면이 부르는 경로"와
 * "프록시가 허용하는 경로"를 맞춰 보는 자리가 없었기 때문이다. 여기가 그 자리다.
 */
const ADMIN_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SCAN_DIRS = ['app', 'lib']
const SKIP_DIRS = new Set(['node_modules', '.next'])

/** `'/admin/<prefix>` 또는 `` `/admin/<prefix> `` 형태를 모두 잡는다. */
const ADMIN_PATH = /['"`]\/admin\/([a-z0-9-]+)/g

function* sourceFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      yield* sourceFiles(full)
      continue
    }
    if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) yield full
  }
}

function referencedPrefixes(): Map<string, string[]> {
  const found = new Map<string, string[]>()
  for (const dir of SCAN_DIRS) {
    for (const file of sourceFiles(join(ADMIN_ROOT, dir))) {
      const source = readFileSync(file, 'utf8')
      for (const match of source.matchAll(ADMIN_PATH)) {
        const prefix = match[1]
        const where = found.get(prefix) ?? []
        if (!where.includes(file)) where.push(file)
        found.set(prefix, where)
      }
    }
  }
  return found
}

test('every admin path the UI calls is allowed by the proxy', () => {
  const referenced = referencedPrefixes()
  assert.ok(referenced.size > 0, 'admin 경로 참조를 하나도 찾지 못했습니다 — 스캐너가 깨졌습니다.')

  for (const [prefix, files] of referenced) {
    assert.ok(
      ALLOWED_PREFIXES.includes(prefix),
      `'/admin/${prefix}'를 부르는데 프록시 allowlist에 없습니다(403으로 끊깁니다). `
        + `참조: ${files.map((file) => file.replace(`${ADMIN_ROOT}/`, '')).join(', ')}`,
    )
  }
})

test('the manual diagnosis path stays allowed', () => {
  // 위 스캐너는 참조가 사라지면 조용해진다. 이 경로는 실제로 사고가 난 곳이라 따로 박아 둔다.
  assert.ok(ALLOWED_PREFIXES.includes('lead-diagnoses'))
})
