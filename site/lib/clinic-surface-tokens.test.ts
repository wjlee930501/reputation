// 병원 공개 표면이 승인된 대표색을 실제로 반영하는지 CSS 수준에서 고정한다.
//
// buildClinicThemeStyle()이 `--clinic-*`를 inline으로 주입해도, 규칙이 파란색을
// 직접 써버리면 병원 브랜드는 화면에 닿지 않는다. 감사에서 확인된 실패 모드가
// 정확히 이것이었고, 렌더 테스트로는 잡히지 않는다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

import { buildClinicThemeStyle } from './clinic-theme.ts'

const GLOBALS = join(process.cwd(), 'app', 'globals.css')

/** 주석 안의 색은 렌더되지 않는다. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ')
}

/** `var(--token, #fallback)`의 fallback은 토큰이 없는 표면에서만 쓰인다. */
function stripVarFallbacks(line: string): string {
  return line.replace(/var\(\s*--[a-z0-9-]+\s*,[^)]*\)/g, 'var(--token)')
}

/**
 * 브랜드 자리를 차지하는 파란 계열인지.
 *
 * 콘텐츠 유형 색(초록·노랑·빨강)과 파랑기 도는 뉴트럴은 브랜드 슬롯이 아니므로
 * 파랑이 빨강과 초록 양쪽을 확실히 앞설 때만 잡는다.
 */
function isBrandBlue(hex: string): boolean {
  const red = Number.parseInt(hex.slice(1, 3), 16)
  const green = Number.parseInt(hex.slice(3, 5), 16)
  const blue = Number.parseInt(hex.slice(5, 7), 16)
  return blue - red >= 24 && blue - green >= 12
}

type Declaration = { line: number; selector: string; text: string }

function clinicDeclarations(): Declaration[] {
  const lines = stripComments(readFileSync(GLOBALS, 'utf8')).split('\n')
  const found: Declaration[] = []
  let selector = ''
  let depth = 0

  lines.forEach((line, index) => {
    if (line.includes('{') && depth === 0) {
      let head = line.split('{')[0].trim()
      for (let back = index - 1; back >= 0 && !head; back -= 1) head = lines[back].trim()
      selector = head
    }
    const opened = (line.match(/\{/g) ?? []).length
    const closed = (line.match(/\}/g) ?? []).length
    const inside = depth > 0 || opened > 0
    depth += opened - closed
    if (inside && selector.includes('clinic')) {
      found.push({ line: index + 1, selector, text: line.trim() })
    }
  })

  return found
}

const DECLARATIONS = clinicDeclarations()

test('the clinic CSS scan actually reaches the public surface rules', () => {
  // 스캔이 비면 아래 단언이 공허하게 통과한다.
  assert.ok(DECLARATIONS.length > 100, `clinic 규칙을 ${DECLARATIONS.length}줄만 찾았다`)
})

test('clinic rules never hard-code a brand blue outside the derived ramp', () => {
  const offenders = DECLARATIONS.filter((declaration) => {
    // ramp 자체는 플랫폼 기본값을 구체적인 색으로 선언해야 한다.
    if (/^\s*--clinic-[a-z0-9-]+\s*:/.test(declaration.text)) return false
    const value = stripVarFallbacks(declaration.text)
    return (value.match(/#[0-9a-fA-F]{6}\b/g) ?? []).some(isBrandBlue)
  }).map((declaration) => `${declaration.line}: ${declaration.selector} — ${declaration.text}`)

  assert.deepEqual(offenders, [])
})

test('every legacy revisit primary slot on the clinic surface is fed by the derived ramp', () => {
  const css = stripComments(readFileSync(GLOBALS, 'utf8'))
  const used = new Set(
    [...css.matchAll(/var\(--(color-revisit-primary-\d+)\)/g)].map((match) => match[1]),
  )

  assert.ok(used.size > 0, 'clinic 표면에서 legacy primary 슬롯을 찾지 못했다')

  const unbridged = [...used].filter(
    (token) => !new RegExp(`--${token}:\\s*var\\(--clinic-revisit-primary-`).test(css),
  )

  assert.deepEqual(unbridged, [])
})

test('the derived ramp fills every clinic bridge slot the CSS reads', () => {
  const css = stripComments(readFileSync(GLOBALS, 'utf8'))
  const bridged = new Set(
    [...css.matchAll(/var\((--clinic-revisit-primary-\d+)\)/g)].map(
      (match) => match[1] as `--clinic-${string}`,
    ),
  )
  const theme = buildClinicThemeStyle({
    brand_primary_color: '#D6A72C',
    brand_accent_color: null,
  })

  assert.ok(bridged.size > 0, 'clinic ramp bridge 슬롯을 찾지 못했다')
  for (const token of bridged) {
    assert.ok(theme[token], `${token}을 buildClinicThemeStyle이 채우지 않는다`)
  }
})
