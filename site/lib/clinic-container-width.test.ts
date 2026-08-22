import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'globals.css'),
  'utf8',
)

/** 셀렉터로 시작하는 규칙 본문(첫 `}`까지)을 모두 돌려준다. */
function rules(selector: string): string[] {
  const found: string[] = []
  const needle = `\n${selector} {`
  let index = CSS.indexOf(needle)
  while (index !== -1) {
    found.push(CSS.slice(index, CSS.indexOf('\n}', index)))
    index = CSS.indexOf(needle, index + 1)
  }
  assert.notEqual(found.length, 0, `규칙을 찾지 못했습니다: ${selector}`)
  return found
}

/**
 * P-B-1 — 공개 병원 홈의 콘텐츠 기준선은 하나다.
 *
 * 감사에서 홈은 세 폭을 섞어 쓰고 있었다: 진료 디렉터리 1344(1440 - 여백 96),
 * 대부분의 섹션 1200, 추천 콘텐츠·푸터 1080. 스크롤을 내리는 동안 좌측 정렬선이
 * 구간마다 옮겨 다녀서, 같은 페이지가 서로 다른 세 페이지처럼 보였다.
 *
 * 폭이 숫자로 흩어져 있으면 다음 섹션을 추가할 때 또 갈라진다 — 모든 컨테이너가
 * 토큰에서 폭을 받는지 여기서 고정한다.
 */

/** 자기 좌우 여백 없이 폭만 잡는 컨테이너. 콘텐츠 폭이 곧 기준선이다. */
const PLAIN_CONTAINERS = [
  '.clinic-section-inner',
  '.clinic-featured-inner',
  '.clinic-footer-inner',
  '.clinic-hero-inner',
  '.clinic-library-hero-inner',
]

/** 자기 좌우 여백(--clinic-rail)을 갖는 컨테이너. 바깥 상자는 여백만큼 더 크다. */
const RAILED_CONTAINERS = [
  '.clinic-header-row',
  '.clinic-hero-editorial-grid',
  '.clinic-hero-fact-rail',
  '.clinic-section-index',
  '.clinic-treatment-directory .clinic-section-inner',
]

test('the container token is declared once, at 1200px', () => {
  assert.match(CSS, /--clinic-max:\s*1200px;/)
  assert.equal(CSS.match(/--clinic-max:/g)?.length, 1)
  assert.match(CSS, /--clinic-shell-max:\s*calc\(var\(--clinic-max\) \+ var\(--clinic-rail\) \* 2\);/)
})

test('plain containers take their width from the single token', () => {
  for (const selector of PLAIN_CONTAINERS) {
    for (const body of rules(selector)) {
      assert.match(
        body,
        /max-width:\s*var\(--clinic-max\)/,
        `${selector}가 기준선 토큰을 쓰지 않습니다 — 이 구간만 정렬선이 어긋납니다.`,
      )
    }
  }
})

test('railed containers reserve room for their own gutter so content still lands on the baseline', () => {
  for (const selector of RAILED_CONTAINERS) {
    const body = rules(selector)[0]
    assert.match(
      body,
      /max-width:\s*(var\(--clinic-shell-max\)|calc\(var\(--clinic-max\) \+ \d+px\))/,
      `${selector}의 바깥 폭이 기준선에서 파생되지 않습니다.`,
    )
  }
})

test('the hero copy sits on the same gutter as the header and the fact rail', () => {
  // 첫 화면 문장의 좌측선이 헤더·팩트 레일과 갈리면 페이지 맨 위에서 바로 어긋난다.
  assert.match(rules('.clinic-hero-editorial-copy')[0], /padding:[^;]*var\(--clinic-rail\)/)
  assert.match(rules('.clinic-hero-fact-rail')[0], /padding:\s*0 var\(--clinic-rail\)/)
  assert.match(rules('.clinic-header-row')[1], /padding:\s*0 var\(--clinic-rail\)/)
})

test('no clinic container hardcodes one of the three widths the audit found', () => {
  // 반응형 분기(@media (max-width: 1080px))는 컨테이너 폭이 아니라 화면 폭이다.
  const declarations = [...CSS.matchAll(/^\s*max-width:\s*(1080px|1200px|1344px|1440px);/gm)]
  assert.deepEqual(
    declarations.map((match) => match[1]),
    [],
    '컨테이너 폭이 다시 숫자로 흩어졌습니다 — --clinic-max에서 파생하세요.',
  )
})
