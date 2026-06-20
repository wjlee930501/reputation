import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const routeSource = readFileSync(join(process.cwd(), 'app/api/auth/login/route.ts'), 'utf8')
const loginPageSource = readFileSync(join(process.cwd(), 'app/login/page.tsx'), 'utf8')

test('login route does not implement a process-local security throttle', () => {
  assert.equal(routeSource.includes('new Map<'), false)
  assert.equal(routeSource.includes('loginAttempts'), false)
  assert.match(routeSource, /authResponse\.status\s*===\s*429/)
})

test('login page announces authentication errors accessibly', () => {
  assert.match(loginPageSource, /role=["']alert["']/)
  assert.match(loginPageSource, /aria-live=["']polite["']/)
})
