// Revisit 콘솔 색상 토큰 정합성.
//
// 정의되지 않은 `var(--color-revisit-*)`는 빌드도 타입체크도 통과하지만 브라우저에서는
// 값이 비어 테두리·본문이 투명하게 렌더된다. CI의 어떤 단계도 이걸 잡지 못하므로
// 여기서 고정한다.
//
// 이전 버전은 하드코딩된 두 파일만 훑고 주석 안의 토큰도 "사용 중"으로 셌다. 이제는
// app/ 전체를 훑고, 주석을 제거한 뒤, 정의↔사용 양방향을 모두 확인한다.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { extname, join } from 'node:path'
import test from 'node:test'

const APP_ROOT = join(process.cwd(), 'app')
const SCANNED_EXTENSIONS = new Set(['.ts', '.tsx', '.css'])
const TOKEN_PREFIX = '--color-revisit-'

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return SCANNED_EXTENSIONS.has(extname(entry.name)) ? [full] : []
  })
}

/** 주석 안의 토큰은 렌더되지 않으므로 "사용"으로 세면 안 된다. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .split('\n')
    .map((line) => line.replace(/(^|\s)\/\/.*$/, '$1'))
    .join('\n')
}

const FILES = sourceFiles(APP_ROOT)
const GLOBALS = join(APP_ROOT, 'globals.css')

const declared = new Set(
  [...stripComments(readFileSync(GLOBALS, 'utf8')).matchAll(/(--color-revisit-[a-z0-9-]+)\s*:/g)].map(
    (match) => match[1],
  ),
)

const referencedBy = new Map<string, string[]>()
for (const file of FILES) {
  for (const match of stripComments(readFileSync(file, 'utf8')).matchAll(
    /var\((--color-revisit-[a-z0-9-]+)/g,
  )) {
    referencedBy.set(match[1], [...(referencedBy.get(match[1]) ?? []), file])
  }
}

test('the token scan actually covers the console surfaces', () => {
  // 스캔 대상이 비어 있으면 아래 두 단언이 공허하게 통과한다.
  assert.ok(FILES.length > 0, 'app/ 아래에서 스캔한 소스가 없다')
  assert.ok(declared.size > 0, 'globals.css에서 찾은 revisit 토큰 정의가 없다')
  assert.ok(referencedBy.size > 0, 'revisit 토큰을 참조하는 파일이 없다')
})

test('every referenced console color token resolves to a global definition', () => {
  const undefinedTokens = [...referencedBy.entries()]
    .filter(([token]) => !declared.has(token))
    .map(([token, files]) => `${token} (${files.join(', ')})`)

  assert.deepEqual(undefinedTokens, [])
})

test('every declared console color token is actually referenced', () => {
  // 정의만 남은 토큰은 팔레트를 오해하게 만든다 — 삭제하거나 실제로 쓰라는 신호.
  const unused = [...declared].filter((token) => !referencedBy.has(token))

  assert.deepEqual(unused, [])
})

test('console color tokens are declared with concrete values, not empty aliases', () => {
  const globals = stripComments(readFileSync(GLOBALS, 'utf8'))

  for (const token of declared) {
    const declaration = new RegExp(`${token}\\s*:\\s*([^;]*);`).exec(globals)
    assert.ok(declaration, `${token} 선언을 찾지 못했다`)
    assert.notEqual(declaration[1].trim(), '', `${token} 값이 비어 있다`)
    // 다른 토큰을 가리키는 별칭이면 그 대상도 정의돼 있어야 한다.
    for (const alias of declaration[1].matchAll(/var\((--[a-z0-9-]+)/g)) {
      assert.ok(
        alias[1].startsWith(TOKEN_PREFIX) ? declared.has(alias[1]) : true,
        `${token}이 정의되지 않은 ${alias[1]}을 가리킨다`,
      )
    }
  }
})
