import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  LEAD_FIRST_CONTACT_TARGET_HOURS,
  describeLeadAging,
  sortLeadsByAttention,
} from './lead-aging.ts'

const now = Date.parse('2026-08-23T12:00:00Z')

function hoursAgo(hours: number): string {
  return new Date(now - hours * 3_600_000).toISOString()
}

test('elapsed time is shown in minutes, hours or days as it grows', () => {
  assert.equal(describeLeadAging({ created_at: hoursAgo(0.25) }, now).elapsedLabel, '15분 전 접수')
  assert.equal(describeLeadAging({ created_at: hoursAgo(3) }, now).elapsedLabel, '3시간 전 접수')
  assert.equal(describeLeadAging({ created_at: hoursAgo(50) }, now).elapsedLabel, '2일 전 접수')
})

test('a fresh lead is inside the first-contact target', () => {
  const aging = describeLeadAging({ created_at: hoursAgo(2) }, now)

  assert.equal(aging.slaState, 'OK')
  assert.equal(aging.slaLabel, '첫 연락 기한 22시간 남음')
})

test('a lead within six hours of the target is called out as due soon', () => {
  const aging = describeLeadAging({ created_at: hoursAgo(20) }, now)

  assert.equal(aging.slaState, 'DUE_SOON')
  assert.match(aging.slaLabel, /4시간 남음/)
})

test('a lead past the target reports how far past, in hours then days', () => {
  assert.equal(
    describeLeadAging({ created_at: hoursAgo(LEAD_FIRST_CONTACT_TARGET_HOURS + 5) }, now).slaLabel,
    '첫 연락 기한 5시간 초과',
  )
  assert.equal(
    describeLeadAging({ created_at: hoursAgo(24 * 4) }, now).slaLabel,
    '첫 연락 기한 3일 초과',
  )
})

test('a converted or dismissed lead carries no deadline — the work is done', () => {
  for (const closed of [
    { created_at: hoursAgo(100), converted_hospital_id: 'h1' },
    { created_at: hoursAgo(100), converted_at: hoursAgo(90) },
    { created_at: hoursAgo(100), status: 'CONVERTED' },
    { created_at: hoursAgo(100), status: 'DISMISSED' },
  ]) {
    const aging = describeLeadAging(closed, now)
    assert.equal(aging.slaState, 'CLOSED', JSON.stringify(closed))
    assert.equal(aging.slaLabel, '처리 완료')
  }
})

test('an unreadable timestamp says so instead of reporting zero hours waiting', () => {
  for (const value of [null, undefined, 'not-a-date']) {
    const aging = describeLeadAging({ created_at: value }, now)
    assert.equal(aging.slaState, 'UNKNOWN')
    assert.equal(aging.elapsedHours, null)
  }
})

test('overdue leads come first, and equal ranks keep the order the server gave', () => {
  const leads = [
    { id: 'fresh', created_at: hoursAgo(1) },
    { id: 'overdue-newer', created_at: hoursAgo(30) },
    { id: 'due-soon', created_at: hoursAgo(20) },
    { id: 'overdue-older', created_at: hoursAgo(80) },
    { id: 'closed', created_at: hoursAgo(200), converted_hospital_id: 'h1' },
  ]

  assert.deepEqual(
    sortLeadsByAttention(leads, now).map((lead) => lead.id),
    ['overdue-newer', 'overdue-older', 'due-soon', 'fresh', 'closed'],
  )
})

test('the leads list shows the elapsed time, the deadline and the test-lead badge', () => {
  const page = readFileSync(new URL('../app/leads/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /describeLeadAging/)
  assert.match(page, /sortLeadsByAttention/)
  assert.match(page, /is_operations_test/)
})
