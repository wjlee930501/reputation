import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { describeLeadAging } from './lead-aging.ts'

const now = Date.parse('2026-08-23T12:00:00Z')

function hoursAgo(hours: number): string {
  return new Date(now - hours * 3_600_000).toISOString()
}

test('elapsed time is shown in minutes, hours or days as it grows', () => {
  assert.equal(describeLeadAging({ created_at: hoursAgo(0.25) }, now).elapsedLabel, '15분 전 접수')
  assert.equal(describeLeadAging({ created_at: hoursAgo(3) }, now).elapsedLabel, '3시간 전 접수')
  assert.equal(describeLeadAging({ created_at: hoursAgo(50) }, now).elapsedLabel, '2일 전 접수')
})

test('an unreadable timestamp says so instead of reporting zero hours waiting', () => {
  for (const value of [null, undefined, 'not-a-date']) {
    const aging = describeLeadAging({ created_at: value }, now)
    assert.equal(aging.elapsedHours, null)
    assert.equal(aging.elapsedLabel, '접수 시각 확인 필요')
  }
})

// 2026-11-20 마케팅 요청(삭제 확정): 첫 연락 기한·기한 초과 표시와 기한 순 정렬을 걷어 낸다.
test('the leads list shows elapsed time without a first-contact deadline', () => {
  const page = readFileSync(new URL('../app/leads/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /describeLeadAging/)
  assert.doesNotMatch(page, /첫 연락 기한|기한 초과|sortLeadsByAttention|slaLabel/)
})

test('operations test leads are hidden by default and can be shown on demand', () => {
  const page = readFileSync(new URL('../app/leads/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /is_operations_test/)
  assert.match(page, /showTestLeads/)
})
