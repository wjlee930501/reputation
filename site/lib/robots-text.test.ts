import assert from 'node:assert/strict'
import test from 'node:test'

import { renderRobotsText } from './robots-text.ts'

const SITEMAP = 'https://clinic.example.com/sitemap.xml'
const DAUM =
  '#DaumWebMasterTool:ec0bfd6a69d68c57e1f24bfe8b935880a1f5d4d1cecb8c30e23ffab73ddb9c47:WoYFtMaR84eZ7MaX1Xjw9A=='

test('robots without Daum keeps existing directives and ends at Sitemap', () => {
  const body = renderRobotsText(SITEMAP)

  assert.match(body, /^User-Agent: \*\nAllow: \/\n/)
  assert.match(body, /Disallow: \/\.well-known\/\n\nUser-Agent: GPTBot/)
  assert.match(body, /User-Agent: Googlebot\nAllow: \/\n/)
  assert.equal(body.endsWith(`Sitemap: ${SITEMAP}\n`), true)
  assert.equal(body.includes('#DaumWebMasterTool:'), false)
})

test('robots appends the exact stored Daum phrase after Sitemap', () => {
  const body = renderRobotsText(SITEMAP, DAUM)

  assert.equal(body.endsWith(`Sitemap: ${SITEMAP}\n${DAUM}\n`), true)
  assert.equal(body.split(DAUM).length - 1, 1)
})

test('robots omits malformed or multiline Daum values without changing directives', () => {
  const plainRobots = renderRobotsText(SITEMAP)

  assert.equal(
    renderRobotsText(SITEMAP, `${DAUM}\nDisallow: /`),
    plainRobots,
  )
  assert.equal(renderRobotsText(SITEMAP, 'not-a-daum-phrase'), plainRobots)
})
