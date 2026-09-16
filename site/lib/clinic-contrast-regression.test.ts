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
