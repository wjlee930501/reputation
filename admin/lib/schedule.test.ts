import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countMonthlyPublishDates,
  DEFAULT_PUBLISH_DAYS_BY_PLAN,
  firstDayOfNextMonthInputValue,
  moveScheduleMonth,
  scheduleMonthLabel,
  localDateInputValue,
  validateScheduleCapacity,
} from './schedule.ts'

test('every current tier has enough default days for its monthly volume', () => {
  assert.equal(validateScheduleCapacity('PLAN_12', DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_12, '2026-06-01'), null)
  assert.equal(validateScheduleCapacity('PLAN_16', DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_16, '2026-06-01'), null)
  assert.equal(validateScheduleCapacity('PLAN_20', DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_20, '2026-06-01'), null)
})

test('schedule validation catches too few publish days before the API call', () => {
  const error = validateScheduleCapacity('PLAN_16', [1, 4], '2026-05-11')

  assert.match(error ?? '', /6개 슬롯/)
  assert.match(error ?? '', /16개 슬롯/)
})

test('localDateInputValue does not shift the selected day through UTC conversion', () => {
  assert.equal(localDateInputValue(new Date(2026, 4, 11, 9, 0, 0)), '2026-05-11')
})

test('firstDayOfNextMonthInputValue starts new schedules without past slots', () => {
  assert.equal(firstDayOfNextMonthInputValue(new Date(2026, 4, 11, 9, 0, 0)), '2026-06-01')
})

test('schedule month navigation crosses year boundaries and uses a readable label', () => {
  assert.equal(scheduleMonthLabel('2026-08-01'), '2026년 8월')
  assert.equal(moveScheduleMonth('2026-01-15', -1), '2025-12-01')
  assert.equal(moveScheduleMonth('2026-12-15', 1), '2027-01-01')
})
