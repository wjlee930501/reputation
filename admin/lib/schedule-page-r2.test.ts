import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(
  new URL('../app/hospitals/[id]/content/ScheduleSection.tsx', import.meta.url),
  'utf8',
)

test('schedule UI confirms date changes while preserving original content', () => {
  assert.match(page, /moveScheduleMonth\(current, -1\)/)
  assert.match(page, /moveScheduleMonth\(current, 1\)/)
  assert.match(page, /role="dialog"/)
  assert.match(page, /기존 원고와 발행 이력은 유지/)
  assert.doesNotMatch(page, /항목이 다시 만들어집니다|이 변경은 자동으로 되돌릴 수 없습니다/)
  assert.match(page, /existing \? null : validateScheduleCapacity/)
  assert.match(page, /setExisting\(await fetchAPI<ScheduleInfo>/)
  assert.match(page, /저장은 완료됐지만 화면을 갱신하지 못했습니다/)
  assert.doesNotMatch(page, /\bconfirm\(/)
})

test('the schedule section never offers a plan selector — the contract owns the plan', () => {
  assert.doesNotMatch(page, /<select[^>]*id="schedule-plan"/)
  assert.match(page, /요금제는 계약 기록에서만 변경/)
  assert.match(page, /useHospitalHeader/)
  assert.match(page, /PLAN_CONTRACT_LABELS/)
})

test('the section keeps the plan defaults, the next-month start date and the contract mismatch notice', () => {
  assert.match(page, /DEFAULT_PUBLISH_DAYS_BY_PLAN/)
  assert.match(page, /firstDayOfNextMonthInputValue/)
  assert.match(page, /PLAN_MISMATCH/)
})
