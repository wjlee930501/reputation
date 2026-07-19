import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const privacyPageSource = readFileSync(new URL('../app/privacy/page.tsx', import.meta.url), 'utf8')

test('privacy disclosure matches Korea runtime and configurable storage/subprocessor regions', () => {
  assert.doesNotMatch(privacyPageSource, /us-central1/)
  assert.match(privacyPageSource, /asia-northeast3/)
  assert.match(privacyPageSource, /멀티리전/)
  assert.match(privacyPageSource, /Slack.*미국/)
})
