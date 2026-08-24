import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('suspended account promotion is locked and test accounts are excluded from the real count', () => {
  const page = readFileSync(new URL('../app/accounts/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /!account\.is_active && account\.role !== 'OWNER'/)
  assert.match(page, /실운영 계정/)
  assert.match(page, /!account\.is_operations_test/)
})
