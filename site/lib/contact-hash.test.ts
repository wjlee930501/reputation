import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeContactHash } from './contact-hash.ts'

test('normalizes a contact fragment containing misplaced UTM parameters', () => {
  assert.equal(
    normalizeContactHash('#contact?utm_source=notice&utm_medium=promotion&utm_campaign=reputation_0914'),
    '#contact',
  )
})

test('normalizes other trailing data after the contact fragment', () => {
  assert.equal(normalizeContactHash('#contact/extra'), '#contact')
  assert.equal(normalizeContactHash('#contact%3Futm_source=notice'), '#contact')
})

test('leaves clean and unrelated fragments alone', () => {
  assert.equal(normalizeContactHash('#contact'), null)
  assert.equal(normalizeContactHash('#faq'), null)
  assert.equal(normalizeContactHash(''), null)
})
