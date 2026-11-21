import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  defaultMonthTarget,
  monthTargetError,
  monthTargetPayload,
  scheduleMonthKey,
  validateScheduleCapacity,
  type ScheduleMonthTarget,
} from './schedule.ts'

const view = (overrides: Partial<ScheduleMonthTarget> = {}): ScheduleMonthTarget => ({
  month: '2026-11',
  contract_quota: 12,
  month_target: 8,
  operator_target: null,
  editable: true,
  ...overrides,
})

test('칸은 편수 없이 저장했을 때 서버가 쓸 값으로 채운다 — 마케터 값, 없으면 계약 편수', () => {
  // 시스템이 8편으로 안분했어도 사람이 저장하면 계약 편수다(2026-11-19 대표 결정).
  assert.equal(defaultMonthTarget(view()), 12)
  assert.equal(defaultMonthTarget(view({ operator_target: 5 })), 5)
  assert.equal(defaultMonthTarget(null), null)
})

test('서버가 쓸 값과 다를 때만 month_target을 보낸다', () => {
  assert.equal(monthTargetPayload(view(), '12'), undefined)
  assert.equal(monthTargetPayload(view(), '6'), 6)
  assert.equal(monthTargetPayload(view(), '0'), 0)
  // 마케터가 정한 값이 있으면 그 값이 기본 — 계약 편수로 되돌리는 것은 보낸다.
  assert.equal(monthTargetPayload(view({ operator_target: 5 }), '5'), undefined)
  assert.equal(monthTargetPayload(view({ operator_target: 5 }), '12'), 12)
  // 계약 첫 달이 아니면 보내지 않는다.
  assert.equal(monthTargetPayload(view({ editable: false }), '6'), undefined)
  assert.equal(monthTargetPayload(null, '6'), undefined)
})

test('0~계약 편수의 정수만 받는다', () => {
  assert.equal(monthTargetError(view(), '0'), null)
  assert.equal(monthTargetError(view(), '12'), null)
  assert.match(monthTargetError(view(), '13') ?? '', /0~12편/)
  assert.match(monthTargetError(view(), '-1') ?? '', /0~12편/)
  assert.match(monthTargetError(view(), '2.5') ?? '', /0~12편/)
  assert.match(monthTargetError(view(), '') ?? '', /0~12편/)
  assert.equal(monthTargetError(view({ editable: false }), '99'), null)
})

test('시작 달 키와 정한 편수만큼의 자리 확인', () => {
  assert.equal(scheduleMonthKey('2026-11-13'), '2026-11')
  assert.equal(scheduleMonthKey('2026-11'), null)
  // 2026-11-13 이후 월·수·금은 8일 — 계약 12편은 하루 1편으로 모자라지만 정한 6편은 놓인다.
  assert.ok(validateScheduleCapacity('PLAN_12', [0, 2, 4], '2026-11-13', 1))
  assert.equal(validateScheduleCapacity('PLAN_12', [0, 2, 4], '2026-11-13', 1, 6), null)
  assert.equal(validateScheduleCapacity('PLAN_12', [0, 2, 4], '2026-11-13', 1, 0), null)
})

test('일정 화면은 시작 달 편수를 서버에서 읽고, 정할 수 없는 달에는 입력 칸을 두지 않는다', () => {
  const section = readFileSync(new URL('../app/hospitals/[id]/content/ScheduleSection.tsx', import.meta.url), 'utf8')
  assert.match(section, /\/schedule\/month-target\?month=\$\{monthKey\}/)
  assert.match(section, /requestedMonthTarget !== undefined \? \{ month_target: requestedMonthTarget \} : \{\}/)
  assert.match(section, /monthView\.editable \?/)
  assert.match(section, /detail\.code\?\.startsWith\('MONTH_TARGET_'\)/)
})
