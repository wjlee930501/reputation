import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import postcss from 'postcss'
import { buildClinicThemeStyle, contrastRatio } from './clinic-theme.ts'

const css = postcss.parse(readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8'))
function colors(selector: string): string[] {
  const result: string[] = []
  css.walkRules(rule => {
    if (rule.selectors.includes(selector)) rule.walkDecls('color', declaration => { result.push(declaration.value) })
  })
  return result
}

test('primary visit helper text preserves the fully opaque theme foreground', () => {
  assert.deepEqual(colors('.clinic-visit-action--primary small'), ['var(--clinic-on-brand)'])
  for (const primary of ['#F05030', '#F0C000', '#D6A72C', '#3C4F5A', '#103080', '#20A0A0', '#17365D', '#282060', '#FFFFFF']) {
    const theme = buildClinicThemeStyle({ brand_primary_color: primary, brand_accent_color: null })
    assert.ok(contrastRatio(theme['--clinic-on-brand'], theme['--clinic-brand-action']) >= 4.5, primary)
  }
})

test('treatment and local category text uses readable ink instead of light status colors', () => {
  for (const selector of ['.clinic-tag--treatment', '.clinic-tag--local']) {
    assert.deepEqual(colors(selector), ['var(--color-revisit-text-title)'])
  }
  for (const background of ['#EEFAF5', '#FFF0EF']) assert.ok(contrastRatio('#303A48', background) >= 4.5)
})

test('small footer metadata has sufficient contrast on the dark footer', () => {
  for (const selector of ['.clinic-footer-col-label', '.clinic-footer-copy']) {
    assert.deepEqual(colors(selector), ['var(--color-revisit-coolgrey-60)'])
  }
  assert.ok(contrastRatio('#B9BFC6', '#303A48') >= 4.5)
})

test('the real browser gate supports optional axe checks and fails on descendant overflow', () => {
  const source = readFileSync(new URL('../../scripts/verify_clinic_layout.mjs', import.meta.url), 'utf8')
  assert.match(source, /browser\.newContext/)
  assert.match(source, /Accessibility violations:/)
  assert.match(source, /check\(metrics\.overflowing\.length === 0/)
})

test('B rail keeps the today cell of same-day clinics on the band text color, not by stylesheet order', () => {
  // 진한 대표색 띠 위 '오늘 진료'가 강조색(띠와 같은 색)으로 묻히지 않게 — 우선순위가 같은 두 규칙의 CSS 순서에 기대지 않는다.
  const layoutB = postcss.parse(readFileSync(new URL('../app/[slug]/_layouts/layout-b.module.css', import.meta.url), 'utf8'))
  const found: string[] = []
  layoutB.walkRules((rule) => {
    if (rule.selectors.some((selector) => selector.includes('.clinic-hero--access-urgent .clinic-hero-fact-rail > div:first-child dd'))) {
      rule.walkDecls('color', (declaration) => {
        found.push(declaration.value)
      })
    }
  })
  assert.deepEqual(found, ['var(--clinic-on-brand-strong)'])
  for (const primary of ['#17365D', '#282060', '#20A0A0']) {
    const theme = buildClinicThemeStyle({ brand_primary_color: primary, brand_accent_color: null })
    assert.ok(contrastRatio(theme['--clinic-on-brand-strong'], theme['--clinic-brand-strong']) >= 4.5, primary)
  }
})
