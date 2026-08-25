import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('suspended account promotion is locked and test accounts are excluded from the real count', () => {
  const page = readFileSync(new URL('../app/accounts/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /!account\.is_active && account\.role !== 'OWNER'/)
  assert.match(page, /실운영 계정/)
  assert.match(page, /!account\.is_operations_test/)
})

test('accounts switch to card rows at tablet width and keep touch targets usable', () => {
  const page = readFileSync(new URL('../app/accounts/page.tsx', import.meta.url), 'utf8')
  const globals = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8')

  assert.match(page, /admin-responsive-table-wrap/)
  assert.match(page, /admin-responsive-table/)
  assert.match(page, /data-primary="true"/)
  assert.match(page, /data-label="계정 작업"/)
  assert.match(globals, /@media \(max-width: 768px\)[\s\S]*?\.admin-responsive-table-wrap/)
  assert.match(page, /className="min-h-11[^"\n]*"/)
})

test('password reset is a labelled modal with focus entry, trap, escape, and restoration', () => {
  const page = readFileSync(new URL('../app/accounts/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /role="dialog"/)
  assert.match(page, /aria-modal="true"/)
  assert.match(page, /aria-labelledby="password-reset-title"/)
  assert.match(page, /htmlFor="account-reset-password"/)
  assert.match(page, /resetPasswordRef\.current\?\.focus\(\)/)
  assert.match(page, /event\.key === 'Escape'/)
  assert.match(page, /event\.key !== 'Tab'/)
  assert.match(page, /requestAnimationFrame\(\(\) => trigger\?\.focus\(\)\)/)
})
