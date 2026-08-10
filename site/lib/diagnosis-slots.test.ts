import assert from 'node:assert/strict'
import test from 'node:test'

import { parseDiagnosisSlots } from './diagnosis-slots.ts'

test('slot counter keeps the server counts used by the numeric quota', () => {
  assert.deepEqual(
    parseDiagnosisSlots({ total: 20, used: 7, remaining: 13 }),
    { total: 20, used: 7, remaining: 13, soldOut: false },
  )
})

test('slot counter rejects malformed or internally inconsistent availability', () => {
  assert.equal(parseDiagnosisSlots({ total: 20, used: 7, remaining: 12 }), null)
  assert.equal(parseDiagnosisSlots({ total: 20, used: -1, remaining: 21 }), null)
  assert.equal(parseDiagnosisSlots(null), null)
})

test('slot counter reports a sold-out day', () => {
  assert.deepEqual(
    parseDiagnosisSlots({ total: 20, used: 20, remaining: 0 }),
    { total: 20, used: 20, remaining: 0, soldOut: true },
  )
})
