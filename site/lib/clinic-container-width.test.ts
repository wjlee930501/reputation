import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import postcss from 'postcss'

import { GLOBALS_CSS_PATH, readClinicCascade, readClinicStyles } from './clinic-stylesheet.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const CASCADE = readClinicCascade()
const STYLES = readClinicStyles()
const GLOBALS = readFileSync(GLOBALS_CSS_PATH, 'utf8')

/**
 * P-B-1 — 공개 병원 홈의 콘텐츠 기준선은 하나다.
 *
 * 감사에서 홈은 세 폭을 섞어 쓰고 있었다: 진료 디렉터리 1344, 대부분의 섹션 1200,
 * 추천 콘텐츠·푸터 1080. 스크롤을 내리는 동안 좌측 정렬선이 구간마다 옮겨 다녀서
 * 같은 페이지가 서로 다른 세 페이지처럼 보였다.
 *
 * 지금은 컨테이너가 `.hub-container` 하나다. 헤더·히어로·모든 섹션·푸터가 같은
 * 폭 토큰과 같은 좌우 여백 토큰을 쓴다. 폭이 숫자로 흩어지면 다음 섹션을 추가할 때
 * 또 갈라지므로 여기서 고정한다.
 */

test('the container token is declared once, at 1200px, and the outer width derives from it', () => {
  assert.match(STYLES, /--clinic-max:\s*1200px;/)
  assert.equal(CASCADE.match(/--clinic-max:/g)?.length, 1, '--clinic-max가 두 곳 이상에서 선언됐다')
  assert.match(STYLES, /--clinic-shell-max:\s*calc\(var\(--clinic-max\) \+ var\(--clinic-rail\) \* 2\);/)
  assert.match(STYLES, /--clinic-rail:\s*var\(--clinic-gutter\);/)
})

test('.hub-container is the only container, and it owns width and gutter together', () => {
  const rule = /\n\.hub-container \{([^}]*)\}/.exec(STYLES)
  assert.ok(rule, '.hub-container 규칙을 찾지 못했습니다')
  assert.match(rule[1], /max-width:\s*var\(--clinic-shell-max\)/)
  assert.match(rule[1], /padding-inline:\s*var\(--clinic-gutter\)/)
  assert.match(rule[1], /margin:\s*0 auto/)

  // 다른 hub 규칙은 기준 폭 토큰을 다시 쓰지 않는다 — 두 번째 컨테이너가 생기는 순간
  // 정렬선이 둘이 된다.
  const root = postcss.parse(STYLES)
  const offenders: string[] = []
  root.walkDecls('max-width', (decl) => {
    const selector = (decl.parent as postcss.Rule).selector
    if (selector === '.hub-container') return
    if (/var\(--clinic-(shell-)?max\)/.test(decl.value)) offenders.push(`${selector} — ${decl.value}`)
  })
  assert.deepEqual(offenders, [])
})

test('every hub section wraps its content in the shared container', () => {
  // 컴포넌트가 컨테이너를 빼먹으면 그 섹션만 화면 가장자리에 붙는다.
  const dir = join(HERE, '..', 'app', '[slug]', '_components')
  const missing: string[] = []
  let scanned = 0
  for (const file of readdirSync(dir)) {
    if (!file.endsWith('.tsx')) continue
    const source = readFileSync(join(dir, file), 'utf8')
    if (!/<(section|header|footer)[^>]*className="hub-(section|hero|header|footer)[^"]*"/.test(source)) continue
    scanned += 1
    if (!source.includes('className="hub-container"')) missing.push(file)
  }
  assert.ok(scanned >= 10, `hub 섹션 컴포넌트를 ${scanned}개만 찾았습니다`)
  assert.deepEqual(missing, [])
})

test('no clinic stylesheet hardcodes one of the container widths the audit found', () => {
  // 반응형 분기(@media (max-width: 1023px))는 컨테이너 폭이 아니라 화면 폭이다.
  const declarations = [...CASCADE.matchAll(/^\s*max-width:\s*(1080px|1200px|1344px|1440px);/gm)]
  assert.deepEqual(
    declarations.map((match) => match[1]),
    [],
    '컨테이너 폭이 다시 숫자로 흩어졌습니다 — --clinic-max에서 파생하세요.',
  )
})

test('legacy sub-page containers still take their width from the same token', () => {
  // 아직 옛 스타일을 쓰는 하위 페이지(의료진·진료·글·오시는 길)도 같은 정렬선 위에 선다.
  const root = postcss.parse(GLOBALS)
  const offenders: string[] = []
  let scanned = 0
  root.walkRules(/\.clinic-[a-z-]*-inner$/, (rule) => {
    rule.walkDecls('max-width', (decl) => {
      scanned += 1
      if (!/var\(--clinic-max\)/.test(decl.value)) offenders.push(`${rule.selector} — ${decl.value}`)
    })
  })
  assert.ok(scanned > 0, '옛 컨테이너 규칙을 찾지 못했습니다')
  assert.deepEqual(offenders, [])
})
