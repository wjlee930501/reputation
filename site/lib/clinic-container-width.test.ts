import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import postcss from 'postcss'

const CSS = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8')
const ROOT = postcss.parse(CSS)
const PLAIN = ['.clinic-section-inner', '.clinic-featured-inner', '.clinic-footer-inner', '.clinic-hero-inner', '.clinic-library-hero-inner']
const RAILED = ['.clinic-header-row', '.clinic-hero-editorial-grid', '.clinic-hero-fact-rail', '.clinic-section-index', '.clinic-article-shell']

function values(selector: string, property: string): string[] {
  const result: string[] = []
  ROOT.walkRules((rule) => {
    if (rule.selectors.includes(selector)) rule.walkDecls(property, (decl) => { result.push(decl.value) })
  })
  return result
}

// A first-match string assertion had passed while a later padding shorthand won.
// Check every declaration, and use verify_clinic_layout.mjs for real browser geometry.
test('the content maximum is declared once and railed width is derived from it', () => {
  assert.deepEqual(values(':root', '--clinic-max'), ['1200px'])
  assert.deepEqual(values(':root', '--clinic-shell-max'), ['calc(var(--clinic-max) + var(--clinic-rail) * 2)'])
})

test('sections use the same responsive gutter as the header and hero', () => {
  assert.deepEqual(values(':root', '--clinic-section-x'), ['var(--clinic-rail)'])
  assert.deepEqual(values(':root', '--clinic-rail'), ['48px', '32px', '20px'])
})

test('inner containers have one maximum and never add a second horizontal inset', () => {
  for (const selector of PLAIN) {
    assert.deepEqual(values(selector, 'max-width'), ['var(--clinic-max)'], selector)
    assert.deepEqual(values(selector, 'padding'), ['0'], selector)
    assert.deepEqual(values(selector, 'padding-inline'), [], selector)
  }
  assert.deepEqual(values('.clinic-treatment-directory .clinic-section-inner', 'padding'), [])
})

test('railed containers share the same outer maximum with no full-bleed breakpoint exception', () => {
  for (const selector of RAILED) {
    assert.deepEqual(values(selector, 'max-width'), ['var(--clinic-shell-max)'], selector)
    const padding = values(selector, 'padding')
    assert.ok(padding.length > 0, selector)
    for (const value of padding) assert.match(value, /var\(--clinic-rail\)/, selector)
    assert.deepEqual(values(selector, 'padding-inline'), [], selector)
  }
})

test('the hero grid owns its gutter exactly once; its copy owns none', () => {
  assert.deepEqual(values('.clinic-hero-editorial-copy', 'padding'), ['0'])
  assert.deepEqual(values('.clinic-hero-editorial-copy', 'padding-left'), [])
  assert.deepEqual(values('.clinic-hero-editorial-copy', 'padding-right'), [])
  assert.doesNotMatch(CSS, /padding-left:\s*calc\(\(100vw - var\(--clinic-max\)/)
})

test('the treatment directory inherits the standard section gutter', () => {
  assert.deepEqual(values('.clinic-treatment-directory', 'padding'), ['var(--clinic-section-y) var(--clinic-section-x)'])
  assert.deepEqual(values('.clinic-treatment-directory .clinic-section-inner', 'max-width'), [])
})

test('no clinic container hardcodes the previously competing widths', () => {
  assert.deepEqual([...CSS.matchAll(/^\s*max-width:\s*(1080px|1200px|1344px|1440px);/gm)], [])
})
