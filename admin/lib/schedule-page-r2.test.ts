import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../app/hospitals/[id]/schedule/page.tsx', import.meta.url), 'utf8')

test('schedule UI has year-month navigation and a destructive replacement dialog', () => {
  assert.match(page, /moveScheduleMonth\(current, -1\)/)
  assert.match(page, /moveScheduleMonth\(current, 1\)/)
  assert.match(page, /role="dialog"/)
  assert.match(page, /미발행 초안 슬롯이 재생성/)
  assert.doesNotMatch(page, /\bconfirm\(/)
})
