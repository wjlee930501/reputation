import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'globals.css'),
  'utf8',
)

/** 셀렉터로 시작하는 규칙 본문(첫 `}`까지)을 돌려준다. */
function rule(selector: string): string {
  const start = CSS.indexOf(`\n${selector} {`)
  assert.notEqual(start, -1, `규칙을 찾지 못했습니다: ${selector}`)
  const end = CSS.indexOf('\n}', start)
  return CSS.slice(start, end)
}

/**
 * 랜딩의 좌측 기준선은 두 계열이 공유한다.
 *
 * - 박스형(market·report·faq): 섹션 자신이 1240 + 안쪽 --shell-pad → 콘텐츠 1160
 * - 풀블리드 틴트(scene·pain·operation·limits): 섹션은 화면 전체, `> *`가 폭을 잡는다
 *
 * `.scene-section`은 예전 이름이 `.preview-section`이었다 — `#scene`이 쓰는데 이름은
 * preview였고, 정작 `#preview`는 `.report-section`을 써서 둘이 뒤바뀐 상태였다.
 *
 * 풀블리드 쪽이 1240을 잡으면 padding 바깥에서 센터링돼 **콘텐츠가 40px 왼쪽**에
 * 선다. 1440px에서는 눈에 안 띄지만 넓은 화면에서 그대로 남아 제목과 내용의 축이
 * 갈린다(2000px에서 실제로 그렇게 나갔다). 두 계열을 --shell-inner로 묶는다.
 */
const FULL_BLEED = ['.scene-section > *', '.pain-section > *', '.operation-section > *', '.limits-section > *']

test('full-bleed sections share the boxed sections content width', () => {
  for (const selector of FULL_BLEED) {
    assert.match(
      rule(selector),
      /max-width:\s*var\(--shell-inner\)/,
      `${selector}가 --shell-inner를 쓰지 않습니다 — 박스형 섹션과 축이 40px 어긋납니다.`,
    )
  }
})

/**
 * 이 스타일시트에서 **세 번** 난 사고다.
 *
 * 부모가 `margin-left/right: auto`로 가운데를 잡아 두는데, 자식 규칙이 뒤에서
 * `margin: 0` 단축 속성을 쓰면 그 auto가 조용히 0이 된다. 같은 파일 안이라
 * 특정도도 같고 경고도 없다. 증상은 "넓은 화면에서만 축이 어긋남"이라 좁은 화면
 * 확인으로는 잡히지 않는다. (`.scene-disclaimer`의 `margin-top: auto`도 같은 이유로
 * 죽어 있었다.)
 */
const CENTERED_CHILDREN = ['.pain-list', '.process-grid', '.limits-grid']

test('centered section children never reset margin with the shorthand', () => {
  for (const selector of CENTERED_CHILDREN) {
    const body = rule(selector)
    assert.doesNotMatch(
      body,
      /^\s*margin:\s/m,
      `${selector}가 margin 단축 속성을 씁니다 — 부모의 auto 가운데 정렬이 죽습니다. margin-block을 쓰세요.`,
    )
    assert.match(body, /margin-block:/, `${selector}에 margin-block 선언이 없습니다.`)
  }
})

test('mobile clinic hero actions override the global full-width button rule', () => {
  const actionRules = [...CSS.matchAll(/\.clinic-hero-editorial-actions \.clinic-btn \{([^}]*)\}/g)]
  const mobileRule = actionRules.at(-1)?.[1] ?? ''

  assert.match(mobileRule, /width:\s*auto/)
  assert.match(mobileRule, /flex:\s*1 1 0/)
})
