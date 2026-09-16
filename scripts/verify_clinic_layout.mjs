/** Rendered layout gate: only accepts a loopback preview with frozen public fixtures.
 * node scripts/verify_clinic_layout.mjs --base-url http://127.0.0.1:3000
 *   --fixtures-dir /path/to/public-data --output-dir /path/to/evidence
 * PLAYWRIGHT_MODULE and CHROME_BIN may point to an isolated test installation.
 * No forms, customer writes, provider calls, or deployment actions are performed.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import { parseArgs } from 'node:util'

const { values } = parseArgs({ options: {
  'base-url': { type: 'string' }, 'fixtures-dir': { type: 'string' },
  'output-dir': { type: 'string' }, widths: { type: 'string', default: '320,390,768,1024,1280,1440,1920' },
  'home-only': { type: 'boolean', default: false },
  a11y: { type: 'boolean', default: false },
} })
assert.ok(values['base-url'] && values['fixtures-dir'] && values['output-dir'], 'Provide base-url, fixtures-dir, output-dir')
const base = new URL(values['base-url'])
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname), 'Only a local preview is allowed')
const widths = values.widths.split(',').map(Number)
assert.ok(widths.every(width => Number.isInteger(width) && width >= 320 && width <= 2560))
const output = path.resolve(values['output-dir'])
await fs.mkdir(output, { recursive: true })
const fixtures = await Promise.all((await fs.readdir(values['fixtures-dir']))
  .filter(name => name.endsWith('.json'))
  .map(async name => JSON.parse(await fs.readFile(path.join(values['fixtures-dir'], name), 'utf8'))))
assert.ok(fixtures.length > 0, 'Empty fixtures cannot produce a green gate')
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_BIN ? { executablePath: process.env.CHROME_BIN } : {}) })
const cases = fixtures.flatMap(fixture => widths.map(width => ({ fixture, width, route: '' })))
if (!values['home-only']) {
  const representatives = [
    [...fixtures].sort((a, b) => b.hospital.name.length - a.hospital.name.length)[0],
    fixtures.find(item => (item.hospital.physicians || []).length > 1),
    [...fixtures].sort((a, b) => b.contents.length - a.contents.length)[0],
  ].filter((value, index, all) => value && all.indexOf(value) === index)
  for (const fixture of representatives) for (const width of [320, 768, 1440]) {
    for (const route of ['doctor', 'visit', 'treatments', 'contents', ...(fixture.detail ? ['contents/' + fixture.detail.id] : [])]) cases.push({ fixture, width, route })
  }
}
const AxeBuilder = values.a11y ? (await import(process.env.AXE_MODULE || '@axe-core/playwright')).default : null
const results = []
try {
  for (const { fixture, width, route } of cases) {
    const slug = fixture.hospital.slug
    const context = await browser.newContext({ viewport: { width, height: 1000 }, deviceScaleFactor: 1 })
    const page = await context.newPage()
    const errors = [], failures = []
    page.on('pageerror', error => errors.push(error.message))
    const url = new URL('/' + slug + (route ? '/' + route : ''), base).href
    try {
      const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 })
      assert.equal(response.status(), 200, 'Page HTTP status')
      await page.locator('.clinic-shell h1').waitFor({ timeout: 15000 })
      await page.evaluate(() => document.fonts.ready)
      const metrics = await page.evaluate(() => {
        const visible = element => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden'
        const rect = element => {
          const box = element.getBoundingClientRect(), style = getComputedStyle(element)
          return { selector: element.className, left: box.left + parseFloat(style.paddingLeft),
            right: box.right - parseFloat(style.paddingRight), width: box.width, height: box.height,
            size: parseFloat(style.fontSize), paddingTop: parseFloat(style.paddingTop),
            paddingBottom: parseFloat(style.paddingBottom), text: element.textContent.trim().slice(0, 80) }
        }
        // Decorative SVG underlays intentionally extend inside clipped image slots.
        // Compare painted horizontal bounds, not an invisible child's unclipped box.
        const overflowsViewport = element => {
          let { left, right } = element.getBoundingClientRect()
          for (let parent = element.parentElement; parent && parent !== document.documentElement; parent = parent.parentElement) {
            if (!['hidden', 'clip', 'auto', 'scroll'].includes(getComputedStyle(parent).overflowX)) continue
            const box = parent.getBoundingClientRect()
            left = Math.max(left, box.left + parent.clientLeft)
            right = Math.min(right, box.left + parent.clientLeft + parent.clientWidth)
          }
          return right > left && (left < -1 || right > innerWidth + 1)
        }
        const all = selector => [...document.querySelectorAll(selector)].filter(visible).map(rect)
        const ids = [...document.querySelectorAll('[id]')].map(element => element.id)
        return {
          viewport: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth,
          header: rect(document.querySelector('.clinic-header-row')),
          containers: all('.clinic-section-inner, .clinic-featured-inner, .clinic-footer-inner, .clinic-library-hero-inner, .clinic-article-shell'),
          hero: all('.clinic-hero-editorial-copy'), h1: all('.clinic-shell h1'), h2: all('.clinic-shell h2'),
          body: all('.clinic-section-note, .clinic-section-lede, .clinic-hero-editorial-lede, .clinic-tx-card-desc--supporting, .clinic-faq-answer p, .clinic-lead-summary, .clinic-content-card-summary'),
          sections: all('.clinic-section:not(.clinic-section--tight), .clinic-featured'),
          touch: all('.clinic-header-cta, .clinic-mobile-actionbar a, .clinic-btn, .clinic-featured-more, .clinic-tx-directory-more, .clinic-faq-more, .clinic-filter-chip'),
          duplicateIds: ids.filter((id, index) => ids.indexOf(id) !== index),
          brokenAnchors: [...document.querySelectorAll('.clinic-section-index a[href^="#"]')]
            .filter(link => !document.getElementById(link.hash.slice(1))).map(link => link.hash),
          overflowing: [...document.querySelectorAll('.clinic-shell *')].filter(visible)
            .filter(overflowsViewport)
            .filter(element => !element.closest('.clinic-markdown-table, .clinic-header-nav-mobile')).slice(0, 12).map(rect),
          jsonld: [...document.querySelectorAll('script[type="application/ld+json"]')].map(element => JSON.parse(element.textContent)),
        }
      })
      const check = (condition, message) => { if (!condition) failures.push(message) }
      check(metrics.scrollWidth <= metrics.viewport + 1, 'Horizontal document overflow')
      check(metrics.overflowing.length === 0, 'Overflowing clinic descendants')
      check(metrics.h1.length === 1, 'Exactly one H1 is required')
      check(metrics.containers.length >= 1, 'Expected clinic containers missing')
      for (const item of metrics.containers) {
        check(Math.abs(item.left - metrics.header.left) <= 1, 'Left rail drift: ' + item.selector)
        check(Math.abs(item.right - metrics.header.right) <= 1, 'Right rail drift: ' + item.selector)
      }
      for (const item of metrics.hero) check(Math.abs(item.left - metrics.header.left) <= 1, 'Hero text rail drift')
      check(metrics.duplicateIds.length === 0, 'Duplicate anchor IDs: ' + metrics.duplicateIds.join(', '))
      check(metrics.brokenAnchors.length === 0, 'Missing section anchor: ' + metrics.brokenAnchors.join(', '))
      for (const item of metrics.body) check(item.size >= 16, 'Body below 16px: ' + item.selector)
      check(metrics.h1[0].size > Math.max(0, ...metrics.h2.map(item => item.size)), 'H1/H2 visual hierarchy inverted')
      if (!route) for (const item of metrics.sections) {
        const expected = width <= 720 ? 48 : width <= 1024 ? 64 : 80
        check(Math.abs(item.paddingTop - expected) <= 1 && Math.abs(item.paddingBottom - expected) <= 1, 'Section rhythm drift: ' + item.selector)
      }
      if (width <= 720) for (const item of metrics.touch) check(item.width >= 43 && item.height >= 43, 'Touch target below 44px: ' + item.selector)
      check(metrics.jsonld.length > 0, 'Public structured data disappeared')
      check(errors.length === 0, 'Browser runtime errors: ' + errors.join('; '))
      let accessibility = null
      if (AxeBuilder && !route && [390, 1440].includes(width)) {
        const scan = await new AxeBuilder({ page }).include('.clinic-shell').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
        accessibility = scan.violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) }))
        check(accessibility.length === 0, 'Accessibility violations: ' + accessibility.map(item => item.id).join(', '))
      }
      const result = { slug, width, route: route || 'home', passed: failures.length === 0, failures, errors, accessibility, ...metrics }
      results.push(result)
      console.log(JSON.stringify({ slug, width, route: result.route, passed: result.passed, failures }))
      if (failures.length) await page.screenshot({ path: path.join(output, slug + '-' + width + '-' + (route || 'home').replaceAll('/', '-') + '-failure.png'), fullPage: true })
    } catch (error) {
      results.push({ slug, width, route: route || 'home', passed: false, failures: [String(error)], errors })
      console.log(JSON.stringify(results.at(-1)))
    } finally { await context.close() }
  }
} finally {
  await browser.close()
  await fs.writeFile(path.join(output, 'layout-results.json'), JSON.stringify(results, null, 2))
}
const summary = { total: results.length, passed: results.filter(item => item.passed).length, failed: results.filter(item => !item.passed).length }
await fs.writeFile(path.join(output, 'layout-summary.json'), JSON.stringify(summary, null, 2))
console.log(JSON.stringify(summary))
if (summary.failed) process.exitCode = 1
