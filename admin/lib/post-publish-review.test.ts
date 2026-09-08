import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')

test('the post-publish confirm button is gated on the server sample flag', () => {
  assert.match(page, /post_publish_review_required === true/)
})

test('brief editing is never offered for published items', () => {
  assert.doesNotMatch(page, /\['DRAFT', 'PUBLISHED'\]\.includes\(selected\.status\)/)
})

test('publish gate 409s surface the server instruction instead of the medical-ad retry copy', () => {
  assert.match(page, /readPublishGateMessage\(e\)/)
  assert.match(page, /HOSPITAL_NOT_PUBLIC/)
  assert.match(page, /SCHEDULE_NOT_SET/)
})
