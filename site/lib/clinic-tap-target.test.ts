import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const CSS = readFileSync(join(HERE, '..', 'app', 'globals.css'), 'utf8')
const HEADER = readFileSync(
  join(HERE, '..', 'app', '[slug]', '_components', 'ClinicHeader.tsx'),
  'utf8',
)

/**
 * P-D-1 — 공개 병원 화면 모바일에서 조작 요소 44개 중 19개가 44px 미만이었다.
 *
 * 가장 심한 것은 헤더 전화 CTA(70×19.5px)였다. 에디토리얼 v5가 헤더 버튼에서
 * padding을 걷어내면서 글자 높이만 남았고, 이 화면에서 가장 중요한 행동인 전화가
 * 가장 누르기 어려웠다. 글자 크기는 그대로 두고 상자만 키운다.
 *
 * 하한은 **두 방향 모두**여야 한다. 높이만 올리면 이동 경로의 "홈"처럼 짧은 라벨은
 * 20×44로 남아 겨냥할 면적이 생기지 않는다 — 이 파일이 막는 회귀가 정확히 그것이다.
 */

const MIN_HEIGHT_44 = /min-height:\s*44px/
const MIN_WIDTH_44 = /min-width:\s*44px/

/** 셀렉터에 적용되는 선언 블록들(목록 중간에 있어도 찾는다). */
function declarationBlocksFor(selector: string): string[] {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const blocks: string[] = []
  for (const pattern of [
    new RegExp(`${escaped}\\s*[,{][^}]*?}`, 'gs'),
    new RegExp(`${escaped},[^{}]*\\{[^}]*?}`, 'gs'),
  ]) {
    for (const match of CSS.matchAll(pattern)) blocks.push(match[0])
  }
  return blocks
}

function declares(selector: string, property: RegExp): boolean {
  return declarationBlocksFor(selector).some((block) => property.test(block))
}

/** 감사에서 44px 미만으로 실측된 조작 요소 전부. */
const MEASURED_CONTROLS = [
  '.clinic-header-cta',
  '.clinic-header-brand',
  '.clinic-header-nav-mobile a',
  '.clinic-hero-fact-rail dd a',
  '.clinic-featured-more',
  '.clinic-answers-all',
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

test('the header phone CTA has a real 44x44 hit box at every width', () => {
  // 헤더 CTA는 데스크톱에서도 19.5px였다 — 모바일 분기에만 두면 절반만 고친 것이다.
  const rule = CSS.slice(CSS.lastIndexOf('\n.clinic-header-cta {'))
  const body = rule.slice(0, rule.indexOf('\n}'))
  assert.match(body, MIN_HEIGHT_44)
  assert.match(body, MIN_WIDTH_44)

  // 전화 CTA는 여전히 tel: 링크이며, 보이는 문구는 그대로다.
  assert.match(HEADER, /className="clinic-header-cta" href=\{`tel:\$\{phone\}`\}/)
  assert.match(HEADER, /<span>전화 상담<\/span>/)
})

test('every control the audit measured under 44px declares both floors', () => {
  for (const selector of MEASURED_CONTROLS) {
    assert.ok(
      declares(selector, MIN_HEIGHT_44),
      `${selector}에 세로 44px 하한이 없습니다 — 모바일에서 누르기 어려운 조작 요소가 남습니다.`,
    )
    assert.ok(
      declares(selector, MIN_WIDTH_44),
      `${selector}에 가로 44px 하한이 없습니다 — 짧은 라벨이 20×44로 남아 겨냥할 면적이 없습니다.`,
    )
  }
})

test('inline text links become inline-flex so both floors actually apply', () => {
  // min-height/min-width는 비치환 inline 요소에 적용되지 않는다. display를 바꾸지
  // 않으면 선언은 남고 상자는 그대로여서, 고쳤다고 착각하기 가장 쉬운 지점이다.
  const block = CSS.slice(CSS.indexOf('.clinic-footer-meta a,'))
  const body = block.slice(0, block.indexOf('\n  }'))
  assert.match(body, /display:\s*inline-flex/)
  assert.match(body, /align-items:\s*center/)
  // 상자가 글자보다 넓어질 때 라벨이 한쪽에 붙지 않게 가로도 가운데로 맞춘다.
  assert.match(body, /justify-content:\s*center/)
})

test('the brand lockup keeps its column layout while gaining the hit area', () => {
  // 세로 flex에서 align-items: center는 이름을 가운데로 밀어 버린다.
  const start = CSS.indexOf('.clinic-header-brand {\n    justify-content: center;')
  assert.notEqual(start, -1, '브랜드 락업 규칙을 찾지 못했습니다.')
  const body = CSS.slice(start, CSS.indexOf('\n  }', start))
  assert.match(body, MIN_HEIGHT_44)
  assert.match(body, MIN_WIDTH_44)
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
  for (const selector of MEASURED_CONTROLS) {
    if (selector === '.clinic-header-cta') continue
    assert.ok(media.includes(selector), `${selector}가 모바일 분기 안에 없습니다.`)
  }
})
