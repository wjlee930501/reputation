import assert from 'node:assert/strict'
import test from 'node:test'

import {
  countMonthlyPublishDates,
  DEFAULT_PUBLISH_DAYS_BY_PLAN,
  firstDayOfNextMonthInputValue,
  localDateInputValue,
  validateScheduleCapacity,
} from './schedule.ts'

test('PLAN_16 default days can create all monthly slots from the next month start', () => {
  assert.equal(countMonthlyPublishDates('2026-06-01', DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_16), 18)
  assert.equal(validateScheduleCapacity('PLAN_16', DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_16, '2026-06-01'), null)
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
