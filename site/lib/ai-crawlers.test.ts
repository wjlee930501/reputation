import assert from 'node:assert/strict'
import test from 'node:test'

import { AI_SEARCH_USER_AGENTS } from './ai-crawlers.ts'

test('robots allowlist explicitly includes answer-time AI search crawlers', () => {
  for (const crawler of [
    'OAI-SearchBot',
    'ChatGPT-User',
    'Perplexity-User',
    'Claude-User',
    'Claude-SearchBot',
    'Googlebot',
    'Bingbot',
  ]) {
    assert.ok(
      AI_SEARCH_USER_AGENTS.includes(crawler as (typeof AI_SEARCH_USER_AGENTS)[number]),
      `${crawler} is missing from the AI crawler allowlist`,
    )
  }
})

test('robots allowlist distinguishes explicit AI search agents from generic wildcard only', () => {
  assert.ok(AI_SEARCH_USER_AGENTS.length >= 8)
  assert.doesNotMatch(AI_SEARCH_USER_AGENTS.join(' '), /\*/)
})
