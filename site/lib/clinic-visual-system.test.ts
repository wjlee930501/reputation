// 병원 공개 표면의 시각 시스템 계약 (Wave 3 P-B / P-C / P-E).
//
// 여기서 검사하는 것들은 전부 "값이 여러 규칙에 숫자로 흩어져 있어서 화면마다
// 달라졌다"는 같은 실패 모드다. 렌더 테스트로는 잡히지 않고, 다음 섹션을 추가할 때
// 조용히 다시 갈라진다. 그래서 스타일시트를 파싱해 캐스케이드 승자를 계산한다.
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import postcss from 'postcss'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS_PATH = join(HERE, '..', 'app', 'globals.css')
const CSS = readFileSync(CSS_PATH, 'utf8')
const ROOT = postcss.parse(CSS, { from: CSS_PATH })

const DESKTOP_WIDTH = 1440
const MOBILE_WIDTH = 390

interface Declaration {
  readonly selector: string
  readonly prop: string
  readonly value: string
  readonly specificity: readonly [number, number, number]
  readonly order: number
}

function specificity(selector: string): [number, number, number] {
  const withoutPseudoElements = selector.replace(/::[\w-]+/g, ' ')
  const ids = withoutPseudoElements.match(/#[\w-]+/g) ?? []
  const classes = withoutPseudoElements.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+(\([^)]*\))?/g) ?? []
  const elements =
    withoutPseudoElements.replace(/[.#[:][^\s>+~]*/g, ' ').match(/\b[a-z][\w-]*\b/g) ?? []
  return [ids.length, classes.length, elements.length]
}

function appliesAtWidth(rule: postcss.Rule, width: number): boolean {
  for (let node: postcss.Container | postcss.Document | undefined = rule.parent; node; node = node.parent) {
    if (node.type !== 'atrule') continue
    const atRule = node as postcss.AtRule
    if (atRule.name !== 'media') continue
    const min = /min-width:\s*(\d+)px/.exec(atRule.params)
    const max = /max-width:\s*(\d+)px/.exec(atRule.params)
    if (min && Number(min[1]) > width) return false
    if (max && Number(max[1]) < width) return false
  }
  return true
}

function declarationsAt(width: number, prop: RegExp): Declaration[] {
  const collected: Declaration[] = []
  let order = 0
  ROOT.walkRules((rule) => {
    const ruleOrder = order++
    if (!appliesAtWidth(rule, width)) return
    rule.walkDecls(prop, (decl) => {
      for (const selector of rule.selectors) {
        collected.push({
          selector,
          prop: decl.prop,
          value: decl.value.trim(),
          specificity: specificity(selector),
          order: ruleOrder,
        })
      }
    })
  })
  return collected
}

function winsOver(a: Declaration, b: Declaration): boolean {
  for (let i = 0; i < 3; i += 1) {
    if (a.specificity[i] !== b.specificity[i]) return a.specificity[i] > b.specificity[i]
  }
  return a.order > b.order
}

/** 이 클래스에 실제로 적용되는 선언. `.clinic-shell` 조상 한정도 같은 대상으로 본다. */
function winner(declarations: Declaration[], className: string): Declaration | null {
  const target = `.${className}`
  const competing = declarations.filter((declaration) => {
    const bare = declaration.selector
      .replace(/::[\w-]+/g, '')
      .replace(/\[[^\]]+\]/g, '')
      .replace(/:(?!:)[\w-]+(\([^)]*\))?/g, '')
      .trim()
    return bare === target || bare.endsWith(` ${target}`)
  })
  if (competing.length === 0) return null
  return competing.reduce((best, candidate) => (winsOver(candidate, best) ? candidate : best))
}

/** `clamp(39px, 3.25vw, 54px)` 같은 값에서 보장되는 최소 크기. */
function minimumPx(value: string): number | null {
  const clamp = /^clamp\(\s*([\d.]+)px/.exec(value)
  if (clamp) return Number(clamp[1])
  const plain = /^([\d.]+)px$/.exec(value)
  return plain ? Number(plain[1]) : null
}

function resolveToken(name: string, width: number): string | null {
  const declarations = declarationsAt(width, new RegExp(`^${name}$`))
  const rootDeclarations = declarations.filter((declaration) => declaration.selector === ':root')
  if (rootDeclarations.length === 0) return null
  return rootDeclarations.reduce((best, candidate) => (winsOver(candidate, best) ? candidate : best))
    .value
}

/** 토큰 참조를 :root 값으로 한 단계 펼친다. 이 스타일시트에는 그 이상 필요 없다. */
function expandTokens(value: string, width: number): string {
  return value.replace(/var\(\s*(--[a-z0-9-]+)\s*(?:,[^)]*)?\)/g, (whole, token: string) => {
    return resolveToken(token, width) ?? whole
  })
}

// ── P-B-2 섹션 세로 리듬 ────────────────────────────────────────────

const SECTION_SURFACES = [
  'clinic-section',
  'clinic-section--tight',
  'clinic-featured',
  'clinic-library-hero',
  'clinic-treatment-directory',
]

test('every section takes its vertical rhythm from the shared tokens', () => {
  for (const width of [DESKTOP_WIDTH, MOBILE_WIDTH]) {
    const paddings = declarationsAt(width, /^padding(-top|-bottom)?$/)
    for (const surface of SECTION_SURFACES) {
      const applied = winner(paddings, surface)
      assert.ok(applied, `${surface}의 padding 선언을 찾지 못했습니다`)
      assert.match(
        applied.value,
        /var\(--clinic-section-y/,
        `${surface}가 ${width}px에서 세로 여백을 숫자로 갖고 있습니다 (${applied.value}) — `
          + '섹션 리듬 토큰에서 파생하세요.',
      )
    }
  }
})

test('no section rule keeps a dead vertical padding behind the tokens', () => {
  // 승자만 보면 죽은 규칙이 남아도 통과한다. Wave 3 이전에는 여섯 규칙이 같은
  // padding을 두고 다퉜고, 그중 어떤 값이 실제로 그려지는지 읽을 수 없었다.
  const paddings = declarationsAt(MOBILE_WIDTH, /^padding(-top|-bottom)?$/)
    .concat(declarationsAt(DESKTOP_WIDTH, /^padding(-top|-bottom)?$/))
  const offenders = paddings
    .filter((declaration) => {
      const bare = declaration.selector.replace(/^.*\s/, '')
      return SECTION_SURFACES.some((surface) => bare === `.${surface}`)
    })
    .filter((declaration) => !declaration.value.includes('var(--clinic-section-y'))
    .map((declaration) => `${declaration.selector} — ${declaration.prop}: ${declaration.value}`)

  assert.deepEqual([...new Set(offenders)], [])
})

// ── P-B-3 조작 요소 반경·굵기 ───────────────────────────────────────

const CONTROLS = [
  'clinic-btn',
  'clinic-filter-chip',
  'clinic-hero-card-call',
  'clinic-visit-action',
]

test('button radii converge on the two control tokens', () => {
  const radii = declarationsAt(DESKTOP_WIDTH, /^border-radius$/)
  const used = new Set<string>()
  for (const control of CONTROLS) {
    const applied = winner(radii, control)
    assert.ok(applied, `${control}의 border-radius 선언을 찾지 못했습니다`)
    assert.match(
      applied.value,
      /var\(--clinic-control-radius(-pill)?\)/,
      `${control}의 반경이 ${applied.value}입니다 — 조작 요소 토큰에서 받으세요.`,
    )
    used.add(applied.value)
  }
  assert.ok(used.size <= 2, `버튼 반경이 ${used.size}갈래입니다: ${[...used].join(', ')}`)
})

test('button label weights converge on the two control tokens', () => {
  const weights = declarationsAt(DESKTOP_WIDTH, /^font-weight$/)
  const used = new Set<string>()
  for (const control of CONTROLS) {
    const applied = winner(weights, control)
    if (!applied) continue
    assert.match(
      applied.value,
      /var\(--clinic-control-weight(-idle)?\)/,
      `${control}의 굵기가 ${applied.value}입니다 — 조작 요소 토큰에서 받으세요.`,
    )
    used.add(applied.value)
  }
  assert.ok(used.size <= 2, `버튼 굵기가 ${used.size}갈래입니다: ${[...used].join(', ')}`)
})

// ── P-B-4 제목 위계 ─────────────────────────────────────────────────

/** 공개 병원 화면의 tsx에서 실제로 h1/h2/h3에 붙은 클래스를 읽는다. */
function headingClassesByLevel(): Record<'1' | '2' | '3', Set<string>> {
  const levels: Record<'1' | '2' | '3', Set<string>> = { '1': new Set(), '2': new Set(), '3': new Set() }
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) {
        walk(full)
        continue
      }
      if (!entry.name.endsWith('.tsx')) continue
      const source = readFileSync(full, 'utf8')
      for (const match of source.matchAll(/<h([123])\s+className="([^"{]+)"/g)) {
        const level = match[1] as '1' | '2' | '3'
        for (const className of match[2].split(/\s+/)) {
          if (className.startsWith('clinic-')) levels[level].add(className)
        }
      }
    }
  }
  walk(join(HERE, '..', 'app', '[slug]'))
  return levels
}

test('the heading scan reaches the clinic pages', () => {
  const levels = headingClassesByLevel()
  assert.ok(levels['1'].size >= 2, `h1 클래스를 ${levels['1'].size}개만 찾았습니다`)
  assert.ok(levels['2'].size >= 3, `h2 클래스를 ${levels['2'].size}개만 찾았습니다`)
  assert.ok(levels['3'].size >= 3, `h3 클래스를 ${levels['3'].size}개만 찾았습니다`)
})

test('h1 stays larger than h2, and h2 larger than h3, at both widths', () => {
  const levels = headingClassesByLevel()
  for (const width of [DESKTOP_WIDTH, MOBILE_WIDTH]) {
    const sizes = declarationsAt(width, /^font-size$/)
    const measured = (level: '1' | '2' | '3') =>
      [...levels[level]]
        .map((className) => {
          const applied = winner(sizes, className)
          if (!applied) return null
          const px = minimumPx(expandTokens(applied.value, width))
          return px === null ? null : { className, px }
        })
        .filter((entry): entry is { className: string; px: number } => entry !== null)

    const h1 = measured('1')
    const h2 = measured('2')
    const h3 = measured('3')
    assert.ok(h1.length > 0 && h2.length > 0 && h3.length > 0, `${width}px에서 크기를 못 읽었습니다`)

    const smallestH1 = h1.reduce((min, entry) => (entry.px < min.px ? entry : min))
    const largestH2 = h2.reduce((max, entry) => (entry.px > max.px ? entry : max))
    const largestH3 = h3.reduce((max, entry) => (entry.px > max.px ? entry : max))

    assert.ok(
      largestH2.px < smallestH1.px,
      `${width}px: h2 .${largestH2.className}(${largestH2.px}px)가 `
        + `h1 .${smallestH1.className}(${smallestH1.px}px)보다 작지 않습니다.`,
    )
    assert.ok(
      largestH3.px < largestH2.px,
      `${width}px: h3 .${largestH3.className}(${largestH3.px}px)가 `
        + `h2 .${largestH2.className}(${largestH2.px}px)보다 작지 않습니다.`,
    )
  }
})

// ── P-B-5 굵기 단계 ────────────────────────────────────────────────

test('font weights snap to the declared steps', () => {
  const offenders: string[] = []
  ROOT.walkDecls('font-weight', (decl) => {
    const literal = /^(\d{3})$/.exec(decl.value.trim())
    if (!literal) return
    if (![400, 500, 600, 700, 800].includes(Number(literal[1]))) {
      offenders.push(`${(decl.parent as postcss.Rule).selector} — font-weight: ${decl.value}`)
    }
  })
  assert.deepEqual(offenders, [], '650·750 같은 중간값은 Pretendard에서 합성 굵기로 렌더된다')
})

// ── P-B-6 그림자 토큰 ──────────────────────────────────────────────

test('the declared shadow tokens are actually used, and never on the clinic surface', () => {
  for (const token of ['--shadow-soft', '--shadow-card']) {
    assert.ok(
      new RegExp(`var\\(${token}\\)`).test(CSS),
      `${token}이 정의만 되어 있고 어디에도 쓰이지 않습니다`,
    )
  }

  // §17 계약 — 병원 공개 표면은 계속 평면이다.
  const offenders: string[] = []
  ROOT.walkDecls('box-shadow', (decl) => {
    const selector = (decl.parent as postcss.Rule).selector
    if (!selector.includes('clinic')) return
    if (decl.value.trim() === 'none' || decl.value.includes('none')) return
    offenders.push(`${selector} — box-shadow: ${decl.value}`)
  })
  assert.deepEqual(offenders, [])
})

// ── P-B-7 승인된 보조색 ────────────────────────────────────────────

test('the approved accent colour reaches the screen', () => {
  const uses = [...CSS.matchAll(/var\(\s*--clinic-accent(-strong)?\b/g)]
  assert.ok(
    uses.length >= 2,
    `--clinic-accent가 화면 규칙 ${uses.length}곳에서만 쓰입니다 — 승인된 보조색이 닿지 않습니다`,
  )
})

// ── P-C-1 빈 그리드 / P-C-2 필터 칩 / P-E-1 캡션 ──────────────────

test('the treatment directory column count follows the item count', () => {
  const columns = declarationsAt(DESKTOP_WIDTH, /^grid-template-columns$/)
  const applied = winner(columns, 'clinic-tx-directory')
  assert.ok(applied)
  assert.match(applied.value, /var\(--clinic-tx-columns/)
})

test('the filter chip selected state is expressed once, by aria-current', () => {
  // 마크업에 없는 `aria-pressed`를 겨냥한 규칙이 선택 상태를 두 곳에 나눠 놓고
  // 그중 한쪽을 죽은 코드로 만들고 있었다.
  assert.doesNotMatch(CSS, /clinic-filter-chip\[aria-pressed/)
  assert.match(CSS, /\.clinic-filter-chip\[aria-current='page'\] \{/)
})

test('the gallery caption scrim is one token, not a gradient fought over by three rules', () => {
  const backgrounds = declarationsAt(DESKTOP_WIDTH, /^background(-image)?$/)
  const applied = winner(backgrounds, 'clinic-gallery-caption')
  assert.ok(applied)
  assert.equal(applied.value, 'var(--clinic-scrim)')
  assert.doesNotMatch(CSS, /clinic-gallery-caption[\s\S]{0,200}linear-gradient/)
})
