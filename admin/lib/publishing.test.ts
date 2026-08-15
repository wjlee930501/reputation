import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { buildManualPublishPayload, normalizePublisherName, resolveAuditActorName } from './publishing.ts'

test('normalizePublisherName rejects empty or whitespace-only screener names', () => {
  assert.equal(normalizePublisherName(''), null)
  assert.equal(normalizePublisherName('   '), null)
})

test('buildManualPublishPayload trims and preserves the explicit screener name', () => {
  assert.deepEqual(buildManualPublishPayload('  김민지 AE  '), { published_by: '김민지 AE' })
})

test('audit actor normalization fails closed when the authenticated name is unavailable', () => {
  assert.equal(resolveAuditActorName(null), null)
  assert.equal(resolveAuditActorName('  '), null)
  assert.equal(resolveAuditActorName(' 김민지 AE '), '김민지 AE')
})

test('content operations use authenticated identity instead of generic audit actors', () => {
  const page = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')
  assert.match(page, /fetchCurrentAccount/)
  assert.doesNotMatch(page, /SYSTEM_MANUAL_RECOVERY|briefApprovedBy\s*\|\|\s*['"]AE['"]/)
})
