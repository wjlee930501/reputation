import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

/**
 * 2026-11-14 공개 페이지 정렬 점검(원래 화면·A·B·C × 7쪽 × 1440/390px)에서 고친 어긋남.
 * 화면 검사 없이도 되돌아가지 않게 규칙의 존재만 확인한다.
 */
const HERE = dirname(fileURLToPath(import.meta.url))
const APP = join(HERE, '..', 'app')
const GLOBALS = readFileSync(join(APP, 'globals.css'), 'utf8')
const LAYOUT = (name: string) => readFileSync(join(APP, '[slug]', '_layouts', `layout-${name}.module.css`), 'utf8')

/** 선택자로 시작하는 마지막 규칙의 본문. 뒤의 규칙이 앞을 이기므로 마지막을 본다. */
function lastRule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))]
  assert.ok(matches.length > 0, `${selector} 규칙이 없다`)
  return matches[matches.length - 1][1]
}

test('추천 글 목록의 분류 칩은 칸 너비로 늘어나지 않는다', () => {
  assert.match(lastRule(GLOBALS, '.clinic-lead-row .clinic-tag'), /justify-self:\s*start/)
})

test('진료 영역 번호와 이름은 글자 기준선을 맞춘다', () => {
  const rule = lastRule(GLOBALS, '.clinic-tx-directory .clinic-tx-card-index,\n.clinic-tx-directory .clinic-tx-card-name')
  assert.match(rule, /align-self:\s*baseline/)
})

test('진료 원칙의 버튼 줄은 문단 바로 아래에 붙는다', () => {
  const band = lastRule(GLOBALS, '.clinic-shell .clinic-principles-band')
  assert.match(band, /grid-template-rows:\s*auto 1fr/)
  assert.match(band, /row-gap:\s*24px/)
})

test('원장 사진이 없으면 머리글자 타일은 정사각형으로 줄인다', () => {
  const rule = lastRule(GLOBALS, '.clinic-shell .clinic-curator-figure .clinic-curator-portrait--monogram')
  assert.match(rule, /aspect-ratio:\s*1 \/ 1/)
})

test('B안은 카드로 바꾼 진료 영역·FAQ 사이를 띄우고, 진료 단계에 카드를 깔지 않는다', () => {
  const b = LAYOUT('b')
  assert.match(lastRule(b, '.shell.shell :global(.clinic-tx-directory)'), /row-gap:\s*16px/)
  assert.match(lastRule(b, '.shell.shell :global(.clinic-tx-directory .clinic-tx-card)'), /padding:/)
  assert.match(lastRule(b, '.shell.shell :global(.clinic-faq-list)'), /gap:\s*12px/)
  assert.doesNotMatch(b, /:global\(\.clinic-flow-node\)\s*\{[^}]*background/)
})

test('A·C안은 바탕을 입힌 헤더 전화 버튼에 안쪽 여백을 준다', () => {
  for (const name of ['a', 'c']) {
    assert.match(lastRule(LAYOUT(name), '.shell :global(.clinic-header-cta)'), /padding:\s*0 18px/, name)
  }
})
