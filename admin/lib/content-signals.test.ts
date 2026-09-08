import assert from 'node:assert/strict'
import test from 'node:test'

import {
  describeTrackingSet,
  isCurrentKoreanMonth,
  latestMentionLabel,
  thisMonthTargets,
} from './content-signals.ts'

test('고정 관측 슬롯에 들어간 질문만 측정 대상으로 말한다', () => {
  assert.equal(describeTrackingSet({ in_tracking_set: true }), '측정 대상')
  assert.equal(describeTrackingSet({ in_tracking_set: false }), '측정 제외')
})

test('이번 달 질문은 대상 월이 같거나 월이 정해지지 않은 질문이다', () => {
  const targets = [
    { target_month: '2026-09' },
    { target_month: '2026-08' },
    { target_month: null },
  ]
  assert.deepEqual(thisMonthTargets(targets, 2026, 9), [targets[0], targets[2]])
  assert.deepEqual(thisMonthTargets(targets, 2026, 8), [targets[1], targets[2]])
})

test('측정 결과가 없으면 0%로 지어내지 않는다', () => {
  assert.equal(latestMentionLabel({ latest_sov_pct: null, last_measured_at: null }), '측정 결과 없음')
  assert.equal(
    latestMentionLabel({ latest_sov_pct: 12, last_measured_at: '2026-09-01T00:00:00Z' }),
    '언급률 12% (9/1 측정)',
  )
  assert.equal(
    latestMentionLabel({ latest_sov_pct: 12.34, last_measured_at: null }),
    '언급률 12.3%',
  )
})

test('"이번 달"은 KST 기준 이번 달일 때만 쓴다', () => {
  const now = new Date('2026-09-30T16:30:00Z') // KST 2026-10-01
  assert.equal(isCurrentKoreanMonth(2026, 10, now), true)
  assert.equal(isCurrentKoreanMonth(2026, 9, now), false)
})

test('측정 날짜는 KST로 읽는다 — UTC 저녁 측정은 다음 날이다', () => {
  assert.equal(
    latestMentionLabel({ latest_sov_pct: 12, last_measured_at: '2026-09-01T16:30:00Z' }),
    '언급률 12% (9/2 측정)',
  )
})
