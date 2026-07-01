import assert from 'node:assert/strict'
import test from 'node:test'

import { buildManualPublishPayload, normalizePublisherName } from './publishing.ts'

test('normalizePublisherName rejects empty or whitespace-only screener names', () => {
  assert.equal(normalizePublisherName(''), null)
  assert.equal(normalizePublisherName('   '), null)
})

test('buildManualPublishPayload trims and preserves the explicit screener name', () => {
  assert.deepEqual(buildManualPublishPayload('  김민지 AE  '), { published_by: '김민지 AE' })
})
