import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import postcss from 'postcss'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS_PATH = join(HERE, '..', 'app', 'globals.css')
const CSS = readFileSync(CSS_PATH, 'utf8')
const HEADER = readFileSync(
  join(HERE, '..', 'app', '[slug]', '_components', 'ClinicHeader.tsx'),
  'utf8',
)

/**
 * P-D-1 — 공개 병원 화면 모바일에서 조작 요소 44개 중 19개가 44px 미만이었다.
 *
 * 이 파일은 세 번 놓친 것을 각각 막는다.
 *
 *  1. 높이만 올리면 짧은 라벨은 20×44로 남는다(이동 경로의 "홈"은 글자 폭 15px 남짓).
 *     → 두 방향 하한을 모두 요구한다.
 *  2. 선언이 있어도 inline 요소에는 적용되지 않는다.
 *     → inline-flex 전환을 요구한다.
 *  3. **뒤에 있다고 이기는 게 아니다.** `.clinic-shell .clinic-filter-chip`의 40px
 *     (특정도 0-2-0)가 파일 끝의 `.clinic-filter-chip` 44px(0-1-0)를 이겨서, 선언은
 *     있는데 Chrome 390px 실측은 75.9×40이었다.
 *     → 문자열이 아니라 **캐스케이드 승자**를 계산해서 검사한다.
 */

const FLOOR_PX = 44
const MOBILE_WIDTH = 390

/** 감사에서 44px 미만으로 실측된 조작 요소 전부. */
const MEASURED_CONTROLS = [
  '.clinic-header-cta',
  '.clinic-header-brand',
  '.clinic-header-nav-mobile a',
  '.clinic-hero-fact-rail dd a',
  '.clinic-featured-more',
  '.clinic-answers-all',
  // Wave 3에서 추가된 링크들. 감사 목록과 같은 하한을 처음부터 요구한다.
  '.clinic-tx-directory-more',
  '.clinic-faq-more',
  '.clinic-faq-all',
  '.clinic-keyfact-link',
  '.clinic-visit-location-link',
  '.clinic-footer-site',
  '.clinic-footer-meta a',
  '.clinic-breadcrumb a',
  '.clinic-official-links a',
  '.clinic-answer-treatment-strip a',
  '.clinic-principles-actions a',
  '.clinic-filter-chip',
  '.clinic-qa-compact-row',
]

interface SizeDeclaration {
  readonly selector: string
  readonly property: 'min-height' | 'min-width'
  readonly px: number
  readonly specificity: readonly [number, number, number]
  readonly order: number
  readonly media: string | null
}

/** CSS 특정도 (id, class·속성·의사클래스, 요소). 이 스타일시트에 필요한 만큼만 센다. */
function specificity(selector: string): [number, number, number] {
  const withoutPseudoElements = selector.replace(/::[\w-]+/g, ' ')
  const ids = withoutPseudoElements.match(/#[\w-]+/g) ?? []
  const classes = withoutPseudoElements.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+(\([^)]*\))?/g) ?? []
  const elements =
    withoutPseudoElements.replace(/[.#[:][^\s>+~]*/g, ' ').match(/\b[a-z][\w-]*\b/g) ?? []
  return [ids.length, classes.length, elements.length]
}

/** 캐스케이드 우선순위 비교: 특정도가 먼저, 같으면 나중에 선언된 쪽. */
function winsOver(a: SizeDeclaration, b: SizeDeclaration): boolean {
  for (let i = 0; i < 3; i += 1) {
    if (a.specificity[i] !== b.specificity[i]) return a.specificity[i] > b.specificity[i]
  }
  return a.order > b.order
}

/** 모바일 폭에서 이 규칙이 살아 있는가. */
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

/** 같은 요소를 겨냥하는 셀렉터인가. 조상 한정과 상태 한정을 모두 같은 대상으로 본다. */
function targetsControl(selector: string, control: string): boolean {
  // `.clinic-filter-chip[aria-current='page']`·`:hover`도 같은 칩이다 — 상태 한정을
  // 떼고 비교해야, 특정도가 더 높은 상태 규칙이 하한을 깎는 경우까지 걸린다.
  const bare = selector.replace(/\[[^\]]+\]/g, '').replace(/:(?!:)[\w-]+(\([^)]*\))?/g, '').trim()
  return bare === control || bare.endsWith(` ${control}`)
}

const SIZE_DECLARATIONS: SizeDeclaration[] = (() => {
  const collected: SizeDeclaration[] = []
  let order = 0
  postcss.parse(CSS, { from: CSS_PATH }).walkRules((rule) => {
    const ruleOrder = order++
    const media = rule.parent?.type === 'atrule' ? (rule.parent as postcss.AtRule).params : null
    const mobile = appliesAtMobileWidth(rule)
    if (!mobile) return
    rule.walkDecls(/^min-(height|width)$/, (decl) => {
      const px = /^([\d.]+)px$/.exec(decl.value.trim())
      if (!px) return
      for (const selector of rule.selectors) {
        collected.push({
          selector,
          property: decl.prop as 'min-height' | 'min-width',
          px: Number(px[1]),
          specificity: specificity(selector),
          order: ruleOrder,
          media,
        })
      }
    })
  })
  return collected
})()

/** 모바일 폭에서 실제로 적용되는 하한. 선언이 없으면 null. */
function effectiveFloor(control: string, property: 'min-height' | 'min-width') {
  const competing = SIZE_DECLARATIONS.filter(
    (declaration) =>
      declaration.property === property && targetsControl(declaration.selector, control),
  )
  if (competing.length === 0) return null
  return competing.reduce((best, candidate) => (winsOver(candidate, best) ? candidate : best))
}

test('the specificity model matches how browsers rank these selectors', () => {
  // 이 계산이 틀리면 아래 검사 전체가 조용히 무의미해진다.
  assert.deepEqual(specificity('.clinic-filter-chip'), [0, 1, 0])
  assert.deepEqual(specificity('.clinic-shell .clinic-filter-chip'), [0, 2, 0])
  assert.deepEqual(specificity('.clinic-shell .clinic-footer-meta a'), [0, 2, 1])
  assert.deepEqual(specificity(".clinic-filter-chip[aria-current='page']"), [0, 2, 0])

  // 특정도가 높으면 앞에 있어도 이긴다 — 이번에 놓친 규칙이 정확히 이 모양이었다.
  const earlierButStronger = {
    selector: '.clinic-shell .clinic-filter-chip',
    property: 'min-height',
    px: 40,
    specificity: [0, 2, 0],
    order: 851,
    media: null,
  } as const satisfies SizeDeclaration
  const laterButWeaker = {
    selector: '.clinic-filter-chip',
    property: 'min-height',
    px: 44,
    specificity: [0, 1, 0],
    order: 1399,
    media: '(max-width: 720px)',
  } as const satisfies SizeDeclaration
  assert.equal(winsOver(earlierButStronger, laterButWeaker), true)
  assert.equal(winsOver(laterButWeaker, earlierButStronger), false)
})

test('every measured control actually resolves to a 44x44 hit box on mobile', () => {
  for (const control of MEASURED_CONTROLS) {
    for (const property of ['min-height', 'min-width'] as const) {
      const winner = effectiveFloor(control, property)
      assert.ok(winner, `${control}에 ${property} 하한 선언이 없습니다.`)
      assert.ok(
        winner.px >= FLOOR_PX,
        `${control}의 ${property}가 ${winner.px}px로 계산됩니다 — `
          + `\`${winner.selector}\`(특정도 ${winner.specificity.join('-')}, 선언 순서 ${winner.order})가 이깁니다. `
          + '뒤에 두는 것만으로는 부족하고 특정도를 맞춰야 합니다.',
      )
    }
  }
})

test('no weaker 44px rule is left sitting behind a stronger smaller one', () => {
  // 승자만 보면 "선언은 있는데 죽어 있는" 규칙이 남아도 통과한다. 44px 미만 선언이
  // 남아 있다면, 그것을 이기는 44px 규칙이 반드시 있어야 한다.
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

test('the header phone CTA keeps its hit box at desktop widths too', () => {
  // 헤더 CTA는 데스크톱에서도 19.5px였다 — 모바일 분기에만 두면 절반만 고친 것이다.
  const rule = CSS.slice(CSS.lastIndexOf('\n.clinic-shell .clinic-header-cta {'))
  const body = rule.slice(0, rule.indexOf('\n}'))
  assert.match(body, /min-height:\s*44px/)
  assert.match(body, /min-width:\s*44px/)

  // 전화 CTA는 여전히 tel: 링크이며, 보이는 문구는 그대로다.
  assert.match(HEADER, /className="clinic-header-cta" href=\{`tel:\$\{phone\}`\}/)
  assert.match(HEADER, /<span>전화 상담<\/span>/)
})

test('inline text links become inline-flex so both floors actually apply', () => {
  // min-height/min-width는 비치환 inline 요소에 적용되지 않는다. display를 바꾸지
  // 않으면 선언은 남고 상자는 그대로여서, 고쳤다고 착각하기 가장 쉬운 지점이다.
  const block = CSS.slice(CSS.indexOf('.clinic-shell .clinic-footer-meta a,'))
  const body = block.slice(0, block.indexOf('\n  }'))
  assert.match(body, /display:\s*inline-flex/)
  assert.match(body, /align-items:\s*center/)
  // 상자가 글자보다 넓어질 때 라벨이 한쪽에 붙지 않게 가로도 가운데로 맞춘다.
  assert.match(body, /justify-content:\s*center/)
})

test('the brand lockup keeps its column layout while gaining the hit area', () => {
  // 세로 flex에서 align-items: center는 이름을 가운데로 밀어 버린다.
  const start = CSS.indexOf('.clinic-shell .clinic-header-brand {\n    justify-content: center;')
  assert.notEqual(start, -1, '브랜드 락업 규칙을 찾지 못했습니다.')
  const body = CSS.slice(start, CSS.indexOf('\n  }', start))
  assert.match(body, /min-height:\s*44px/)
  assert.match(body, /min-width:\s*44px/)
  assert.doesNotMatch(body, /align-items:/)
})

test('raising the hit area does not raise the visible type size', () => {
  // 폰트를 키우면 정보 밀도가 무너진다. 이 블록은 상자만 다뤄야 한다.
  const block = CSS.slice(CSS.indexOf('손가락으로 누르는 것은 44px다 — 공개 병원 화면'))
  assert.doesNotMatch(block, /font-size:/)
})

test('the mobile floors stay inside the mobile breakpoint so desktop keeps its look', () => {
  // 헤더 CTA만 전 구간이고, 나머지는 720px 이하에서만 상자를 키운다.
  const block = CSS.slice(CSS.indexOf('손가락으로 누르는 것은 44px다 — 공개 병원 화면'))
  const media = block.slice(block.indexOf('@media (max-width: 720px)'))
  for (const control of MEASURED_CONTROLS) {
    if (control === '.clinic-header-cta') continue
    assert.ok(media.includes(control), `${control}가 모바일 분기 안에 없습니다.`)
  }
})
