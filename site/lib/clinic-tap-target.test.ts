import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import postcss from 'postcss'

import { readClinicCascade } from './clinic-stylesheet.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readClinicCascade()
const HEADER = readFileSync(
  join(HERE, '..', 'app', '[slug]', '_components', 'ClinicHeader.tsx'),
  'utf8',
)

/**
 * P-D-1 — 공개 병원 화면 모바일에서 조작 요소 44개 중 19개가 44px 미만이었다.
 *
 * 이 파일은 세 번 놓친 것을 각각 막는다.
 *
 *  1. 높이만 올리면 짧은 라벨은 20×44로 남는다.
 *     → 두 방향 하한을 모두 요구한다.
 *  2. 선언이 있어도 inline 요소에는 적용되지 않는다.
 *     → inline-flex/flex 전환을 요구한다.
 *  3. **뒤에 있다고 이기는 게 아니다.** 특정도가 높은 앞 규칙이 뒤의 44px를 이긴다.
 *     → 문자열이 아니라 **캐스케이드 승자**를 계산해서 검사한다.
 *
 * 하한은 토큰(`--clinic-control-height: 44px`)으로 적으므로 토큰을 펼쳐서 잰다.
 */

const FLOOR_PX = 44
const MOBILE_WIDTH = 390

/** 병원 홈·공용 chrome의 조작 요소 전부. 새 조작 요소를 추가하면 여기에도 적는다. */
const MEASURED_CONTROLS = [
  '.hub-header-cta',
  '.hub-header-brand',
  '.hub-header-nav a',
  '.hub-header-subnav a',
  '.hub-actionbar a',
  '.hub-btn',
  '.hub-more',
  '.hub-chip-link',
  '.hub-fact dd a',
  '.hub-doctor-career-toggle',
  '.hub-qa',
  '.hub-qa-compact',
  '.hub-contact-link',
  '.hub-footer-meta a',
  // 아직 옛 레이어를 쓰는 하위 페이지의 조작 요소.
  '.clinic-breadcrumb a',
  '.clinic-filter-chip',
  '.clinic-visit-location-link',
]

/** 폭 방향 하한은 짧은 라벨의 inline 요소에만 뜻이 있다. 행 전체를 쓰는 요소는 뺀다. */
const FULL_WIDTH_CONTROLS = new Set(['.hub-qa', '.hub-actionbar a'])

interface SizeDeclaration {
  readonly selector: string
  readonly property: 'min-height' | 'min-width'
  readonly px: number
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

function winsOver(a: SizeDeclaration, b: SizeDeclaration): boolean {
  for (let i = 0; i < 3; i += 1) {
    if (a.specificity[i] !== b.specificity[i]) return a.specificity[i] > b.specificity[i]
  }
  return a.order > b.order
}

function appliesAtMobileWidth(rule: postcss.Rule): boolean {
  for (let node: postcss.Container | postcss.Document | undefined = rule.parent; node; node = node.parent) {
    if (node.type !== 'atrule') continue
    const atRule = node as postcss.AtRule
    if (atRule.name !== 'media') continue
    const min = /min-width:\s*(\d+)px/.exec(atRule.params)
    const max = /max-width:\s*(\d+)px/.exec(atRule.params)
    if (min && Number(min[1]) > MOBILE_WIDTH) return false
    if (max && Number(max[1]) < MOBILE_WIDTH) return false
  }
  return true
}

function targetsControl(selector: string, control: string): boolean {
  const bare = selector.replace(/\[[^\]]+\]/g, '').replace(/:(?!:)[\w-]+(\([^)]*\))?/g, '').trim()
  return bare === control || bare.endsWith(` ${control}`)
}

const ROOT = postcss.parse(CSS)

/** `:root`의 토큰을 한 단계 펼친다 — 하한은 `var(--clinic-control-height)`로 적혀 있다. */
const TOKENS = new Map<string, string>()
ROOT.walkRules(':root', (rule) => {
  if (!appliesAtMobileWidth(rule)) return
  rule.walkDecls((decl) => {
    TOKENS.set(decl.prop, decl.value.trim())
  })
})
function resolvePx(value: string): number | null {
  const expanded = value.replace(/var\(\s*(--[a-z0-9-]+)\s*\)/g, (whole, token: string) => TOKENS.get(token) ?? whole)
  const px = /^([\d.]+)px$/.exec(expanded.trim())
  return px ? Number(px[1]) : null
}

const SIZE_DECLARATIONS: SizeDeclaration[] = (() => {
  const collected: SizeDeclaration[] = []
  let order = 0
  ROOT.walkRules((rule) => {
    const ruleOrder = order++
    if (!appliesAtMobileWidth(rule)) return
    rule.walkDecls(/^min-(height|width)$/, (decl) => {
      const px = resolvePx(decl.value)
      if (px === null) return
      for (const selector of rule.selectors) {
        collected.push({
          selector,
          property: decl.prop as 'min-height' | 'min-width',
          px,
          specificity: specificity(selector),
          order: ruleOrder,
        })
      }
    })
  })
  return collected
})()

function effectiveFloor(control: string, property: 'min-height' | 'min-width') {
  const competing = SIZE_DECLARATIONS.filter(
    (declaration) =>
      declaration.property === property && targetsControl(declaration.selector, control),
  )
  if (competing.length === 0) return null
  return competing.reduce((best, candidate) => (winsOver(candidate, best) ? candidate : best))
}

test('the specificity model matches how browsers rank these selectors', () => {
  assert.deepEqual(specificity('.hub-btn'), [0, 1, 0])
  assert.deepEqual(specificity('.clinic-shell .hub-btn'), [0, 2, 0])
  assert.deepEqual(specificity('.hub-footer-meta a'), [0, 1, 1])
  assert.deepEqual(specificity(".hub-header-nav [aria-current='page']"), [0, 2, 0])
})

test('the control height token resolves to the 44px floor', () => {
  assert.equal(TOKENS.get('--clinic-control-height'), '44px')
})

test('every measured control actually resolves to a 44px hit box on mobile', () => {
  for (const control of MEASURED_CONTROLS) {
    const properties: Array<'min-height' | 'min-width'> = FULL_WIDTH_CONTROLS.has(control)
      ? ['min-height']
      : ['min-height', 'min-width']
    for (const property of properties) {
      const winner = effectiveFloor(control, property)
      assert.ok(winner, `${control}에 ${property} 하한 선언이 없습니다.`)
      assert.ok(
        winner.px >= FLOOR_PX,
        `${control}의 ${property}가 ${winner.px}px로 계산됩니다 — `
          + `\`${winner.selector}\`(특정도 ${winner.specificity.join('-')}, 선언 순서 ${winner.order})가 이깁니다.`,
      )
    }
  }
})

test('no weaker 44px rule is left sitting behind a stronger smaller one', () => {
  for (const control of MEASURED_CONTROLS) {
    for (const property of ['min-height', 'min-width'] as const) {
      const competing = SIZE_DECLARATIONS.filter(
        (declaration) =>
          declaration.property === property && targetsControl(declaration.selector, control),
      )
      const winner = effectiveFloor(control, property)
      for (const loser of competing.filter((declaration) => declaration.px < FLOOR_PX)) {
        assert.ok(
          winner && winsOver(winner, loser),
          `${control}: \`${loser.selector}\`의 ${loser.px}px를 이기는 ${FLOOR_PX}px 규칙이 없습니다.`,
        )
      }
    }
  }
})

test('the header phone CTA keeps its hit box at desktop widths too, and stays a tel: link', () => {
  const rule = /\n\.hub-header-cta \{([^}]*)\}/.exec(CSS)
  assert.ok(rule, '.hub-header-cta 규칙을 찾지 못했습니다')
  assert.match(rule[1], /min-height:\s*var\(--clinic-control-height\)/)
  assert.match(rule[1], /min-width:\s*var\(--clinic-control-height\)/)
  assert.match(HEADER, /className="hub-header-cta" href=\{`tel:\$\{phone\}`\}/)
  assert.match(HEADER, /<span>전화 상담<\/span>/)
})

test('inline text links become inline-flex so both floors actually apply', () => {
  // min-height/min-width는 비치환 inline 요소에 적용되지 않는다.
  for (const selector of ['.hub-footer-meta a', '.hub-fact dd a', '.hub-contact-link', '.hub-more', '.hub-chip-link']) {
    const rule = new RegExp(`\\n${selector.replace(/[.]/g, '\\.')} \\{([^}]*)\\}`).exec(CSS)
    assert.ok(rule, `${selector} 규칙을 찾지 못했습니다`)
    assert.match(rule[1], /display:\s*inline-flex/, `${selector}가 inline-flex가 아닙니다`)
  }
})

test('the brand lockup keeps the hit area without centring its text', () => {
  const rule = /\n\.hub-header-brand \{([^}]*)\}/.exec(CSS)
  assert.ok(rule)
  assert.match(rule[1], /min-height:\s*var\(--clinic-control-height\)/)
  assert.match(rule[1], /display:\s*inline-flex/)
})
