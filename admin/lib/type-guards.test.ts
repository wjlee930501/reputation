import assert from 'node:assert/strict'
import test from 'node:test'

import { isRecord } from './type-guards.ts'

test('isRecord accepts plain and class-backed objects', () => {
  assert.equal(isRecord({}), true)
  assert.equal(isRecord(new Date()), true)
})

test('isRecord rejects null, arrays, and scalar values', () => {
  assert.equal(isRecord(null), false)
  assert.equal(isRecord([]), false)
  assert.equal(isRecord('value'), false)
  assert.equal(isRecord(1), false)
})
