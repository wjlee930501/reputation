import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../app/hospitals/[id]/schedule/page.tsx', import.meta.url), 'utf8')

test('schedule UI has year-month navigation and a destructive replacement dialog', () => {
  assert.match(page, /moveScheduleMonth\(current, -1\)/)
  assert.match(page, /moveScheduleMonth\(current, 1\)/)
  assert.match(page, /role="dialog"/)
  assert.match(page, /아직 발행하지 않은 콘텐츠 항목이 다시 만들어집니다/)
  assert.doesNotMatch(page, /\bconfirm\(/)
})

test('the schedule page never offers a plan selector — the contract owns the plan', () => {
  assert.doesNotMatch(page, /<select[^>]*id="schedule-plan"/)
  assert.match(page, /요금제는 계약 기록에서만 변경/)
})
