import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveDefaultPublisherName } from './publisher-identity.ts'

test('resolveDefaultPublisherName prefers the logged-in account name when no override exists', () => {
  assert.equal(resolveDefaultPublisherName('김민지 AE', null), '김민지 AE')
})

test('resolveDefaultPublisherName falls back to empty string when nothing is known', () => {
  assert.equal(resolveDefaultPublisherName(null, null), '')
})

test('resolveDefaultPublisherName honors an explicit override, even an empty one the AE cleared', () => {
  assert.equal(resolveDefaultPublisherName('김민지 AE', '박서준 AE'), '박서준 AE')
  assert.equal(resolveDefaultPublisherName('김민지 AE', ''), '')
})
