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

import {
  CLINIC_LAYOUT_PATH,
  CLINIC_STYLE_FILES,
  CLINIC_STYLES_DIR,
  readClinicCascade,
  readClinicStyles,
} from './clinic-stylesheet.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readClinicCascade()
const STYLES = readClinicStyles()
const ROOT = postcss.parse(CSS)

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

/** 이 클래스에 실제로 적용되는 선언. 조상 한정도 같은 대상으로 본다. */
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

/** 토큰 참조를 :root 값으로 펼친다. 토큰이 토큰을 가리키는 경우도 있어 세 번까지 편다. */
function expandTokens(value: string, width: number): string {
  let expanded = value
  for (let pass = 0; pass < 3; pass += 1) {
    expanded = expanded.replace(/var\(\s*(--[a-z0-9-]+)\s*(?:,[^)]*)?\)/g, (whole, token: string) => {
      return resolveToken(token, width) ?? whole
    })
  }
  return expanded
}

// ── 레이어 순서 ──────────────────────────────────────────────────────

test('the layout imports the style layer in the order the tests read it', () => {
  // 캐스케이드 순서가 곧 우선순위다. 두 목록이 어긋나면 테스트가 보는 승자와
  // 브라우저가 보는 승자가 다르다.
  const layout = readFileSync(CLINIC_LAYOUT_PATH, 'utf8')
  const imported = [...layout.matchAll(/import '\.\/_styles\/([a-z-]+\.css)'/g)].map((match) => match[1])
  assert.deepEqual(imported, [...CLINIC_STYLE_FILES])
  assert.deepEqual(readdirSync(CLINIC_STYLES_DIR).filter((file) => file.endsWith('.css')).sort(), [...CLINIC_STYLE_FILES].sort())
})

test('each hub selector is defined in exactly one style file', () => {
  // 같은 셀렉터가 두 파일에 있으면 "뒤에 있는 쪽이 이긴다"가 다시 시작된다 —
  // 옛 스타일시트가 무너진 방식이다. 같은 파일 안의 미디어 분기는 허용한다.
  const owners = new Map<string, Set<string>>()
  for (const file of CLINIC_STYLE_FILES) {
    const root = postcss.parse(readFileSync(join(CLINIC_STYLES_DIR, file), 'utf8'))
    root.walkRules((rule) => {
      for (const selector of rule.selectors) {
        if (!selector.includes('.hub-') && !selector.includes('.clinic-shell')) continue
        const set = owners.get(selector) ?? new Set<string>()
        set.add(file)
        owners.set(selector, set)
      }
    })
  }
  const shared = [...owners.entries()].filter(([, files]) => files.size > 1).map(([selector, files]) => `${selector}: ${[...files].join(', ')}`)
  assert.deepEqual(shared, [])
})

// ── P-B-2 섹션 세로 리듬 ────────────────────────────────────────────

const SECTION_SURFACES = [
  'hub-section',
  'hub-section--tight',
  // 아직 옛 레이어를 쓰는 하위 페이지.
  'clinic-section',
  'clinic-section--tight',
  'clinic-library-hero',
]

test('every section takes its vertical rhythm from the shared tokens', () => {
  for (const width of [DESKTOP_WIDTH, MOBILE_WIDTH]) {
    const paddings = declarationsAt(width, /^padding(-top|-bottom|-block)?$/)
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
  const paddings = declarationsAt(MOBILE_WIDTH, /^padding(-top|-bottom|-block)?$/)
    .concat(declarationsAt(DESKTOP_WIDTH, /^padding(-top|-bottom|-block)?$/))
  const offenders = paddings
    .filter((declaration) => {
      const bare = declaration.selector.replace(/^.*\s/, '')
      return SECTION_SURFACES.some((surface) => bare === `.${surface}`)
    })
    .filter((declaration) => !declaration.value.includes('var(--clinic-section-y'))
    .map((declaration) => `${declaration.selector} — ${declaration.prop}: ${declaration.value}`)

  assert.deepEqual([...new Set(offenders)], [])
})

test('hub components never write spacing as raw numbers', () => {
  // 간격은 4px 단계 토큰에서만 온다. 숫자가 다시 들어오면 리듬이 파일마다 갈라진다.
  // 예외: 0, 1px 선, 아이콘·초상·번호 원 같은 고정 크기(width/height), 글자 크기.
  const root = postcss.parse(STYLES)
  const offenders: string[] = []
  root.walkDecls(/^(margin|padding|gap|row-gap|column-gap)(-[a-z]+)?$/, (decl) => {
    const selector = (decl.parent as postcss.Rule).selector
    if (selector === ':root') return
    const literal = decl.value.replace(/var\([^)]*\)/g, '').replace(/calc\([^)]*\)/g, '')
    const numbers = literal.match(/\b\d+(\.\d+)?px\b/g) ?? []
    if (numbers.some((px) => px !== '0px' && px !== '1px')) offenders.push(`${selector} — ${decl.prop}: ${decl.value}`)
  })
  assert.deepEqual(offenders, [])
})

// ── P-B-3 조작 요소 반경·굵기 ───────────────────────────────────────

const CONTROLS = [
  'hub-btn',
  'hub-chip-link',
  'hub-header-cta',
  'clinic-filter-chip',
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
          if (className.startsWith('clinic-') || className.startsWith('hub-')) levels[level].add(className)
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

// ── P-B-6 그림자 ───────────────────────────────────────────────────

test('the clinic surface stays flat — no shadows, no gradients, no important', () => {
  const offenders: string[] = []
  ROOT.walkDecls((decl) => {
    const selector = (decl.parent as postcss.Rule).selector ?? ''
    const onClinic = selector.includes('clinic') || selector.includes('hub-')
    if (!onClinic) return
    if (decl.prop === 'box-shadow' && !decl.value.includes('none')) offenders.push(`${selector} — box-shadow`)
    if (/gradient\(/.test(decl.value)) offenders.push(`${selector} — ${decl.prop}: gradient`)
    // 옛 레이어의 anti-slop 가드(`background-image: none !important`)는 장식을 끄는
    // 쪽이라 허용한다. 값을 강제하는 !important만 잡는다.
    if (decl.important && decl.value.trim() !== 'none') offenders.push(`${selector} — ${decl.prop} !important`)
  })
  assert.deepEqual(offenders, [])
})

// ── P-B-7 승인된 보조색 ────────────────────────────────────────────

test('the approved accent colour reaches the screen, in small supporting labels only', () => {
  const uses = [...STYLES.matchAll(/var\(\s*--clinic-accent(-strong)?\b/g)]
  assert.ok(
    uses.length >= 2,
    `--clinic-accent가 화면 규칙 ${uses.length}곳에서만 쓰입니다 — 승인된 보조색이 닿지 않습니다`,
  )
  // 보조색은 5% 규칙이다 — 버튼 배경이나 제목 같은 큰 면에 닿으면 안 된다.
  const root = postcss.parse(STYLES)
  const offenders: string[] = []
  root.walkDecls((decl) => {
    if (!/--clinic-accent/.test(decl.value)) return
    if (decl.prop !== 'color') offenders.push(`${(decl.parent as postcss.Rule).selector} — ${decl.prop}`)
  })
  assert.deepEqual(offenders, [])
})

// ── P-C-1 빈 그리드 / P-C-2 필터 칩 / P-E-1 캡션 ──────────────────

test('the treatment directory column count follows the item count', () => {
  const columns = declarationsAt(DESKTOP_WIDTH, /^grid-template-columns$/)
  const applied = winner(columns, 'hub-tx-grid')
  assert.ok(applied)
  assert.match(applied.value, /var\(--clinic-tx-columns/)
})

test('the gallery grid shape is decided by the photo count, and never strands one tile', () => {
  // 어떤 장수에서도 마지막 줄에 타일 하나가 홀로 남지 않는다 — 규칙이 장수별로 있어야 한다.
  for (const count of [3, 4, 5, 6, 7, 8]) {
    assert.match(STYLES, new RegExp(`\\.hub-gallery\\[data-count='${count}'\\]`), `${count}장 규칙이 없습니다`)
  }
})

test('the filter chip selected state is expressed once, by aria-current', () => {
  assert.doesNotMatch(CSS, /clinic-filter-chip\[aria-pressed/)
  assert.match(CSS, /\.clinic-filter-chip\[aria-current='page'\] \{/)
})

test('the gallery caption scrim is one token, not a gradient fought over by three rules', () => {
  const backgrounds = declarationsAt(DESKTOP_WIDTH, /^background(-image)?$/)
  const applied = winner(backgrounds, 'hub-gallery-caption')
  assert.ok(applied)
  assert.equal(applied.value, 'var(--clinic-scrim)')
})

// ── 면 교차 ────────────────────────────────────────────────────────

test('section backgrounds alternate by order, never by component', () => {
  // 컴포넌트가 자기 배경을 정하면 렌더되지 않는 섹션이 생길 때 같은 색이 연달아 온다.
  assert.match(STYLES, /\.hub-main > \.hub-section:nth-of-type\(even\) \{[^}]*background:\s*var\(--clinic-surface-alt\)/)
  const root = postcss.parse(STYLES)
  const offenders: string[] = []
  root.walkRules(/^\.hub-section(--[a-z]+)?$/, (rule) => {
    rule.walkDecls(/^background(-color)?$/, () => {
      offenders.push(rule.selector)
    })
  })
  assert.deepEqual(offenders, [])
})
