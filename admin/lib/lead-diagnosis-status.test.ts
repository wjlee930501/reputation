import assert from 'node:assert/strict'
import test from 'node:test'

import {
  type LeadDiagnosisSummary,
  canReleaseLock,
  canRetryDelivery,
  diagnosisBadges,
  diagnosisHint,
  leadNeedsAttention,
  needsAttention,
} from './lead-diagnosis-status.ts'

function make(overrides: Partial<LeadDiagnosisSummary> = {}): LeadDiagnosisSummary {
  return {
    id: 'd1',
    execution_status: 'SUCCEEDED',
    report_status: 'READY',
    delivery_status: 'SENT',
    ...overrides,
  }
}

// ── 3축을 접지 않는다 ────────────────────────────────────────────────
test('all three axes are always shown', () => {
  const badges = diagnosisBadges(make())
  assert.deepEqual(
    badges.map((b) => b.axis),
    ['측정', '리포트', '발송'],
  )
})

test('PARTIAL measurement is not painted as success', () => {
  // 초록으로 칠하면 "표본이 계획보다 적다"는 사실이 화면에서 사라진다.
  const badge = diagnosisBadges(make({ execution_status: 'PARTIAL' }))[0]
  assert.equal(badge.tone, 'warn')
  assert.equal(badge.label, '일부 실패')
})

test('an unknown status falls back to the raw value instead of vanishing', () => {
  const badge = diagnosisBadges(make({ delivery_status: 'WAT' }))[2]
  assert.equal(badge.label, 'WAT')
  assert.equal(badge.tone, 'muted')
})

// ── 확인 필요 판정 ───────────────────────────────────────────────────
test('backend needs_attention wins when present', () => {
  assert.equal(needsAttention(make({ needs_attention: true })), true)
  assert.equal(
    needsAttention(make({ delivery_status: 'FAILED', needs_attention: false })),
    false,
  )
})

test('without the backend flag each terminal failure counts', () => {
  assert.equal(needsAttention(make({ execution_status: 'FAILED' })), true)
  assert.equal(needsAttention(make({ report_status: 'BLOCKED' })), true)
  assert.equal(needsAttention(make({ delivery_status: 'FAILED' })), true)
  assert.equal(needsAttention(make()), false)
})

test('a lead is flagged when any of its diagnoses needs attention', () => {
  assert.equal(leadNeedsAttention([make(), make({ delivery_status: 'FAILED' })]), true)
  assert.equal(leadNeedsAttention([make()]), false)
  assert.equal(leadNeedsAttention(undefined), false)
})

// ── 액션 노출 조건 ───────────────────────────────────────────────────
test('retry is offered only when a report exists to send', () => {
  assert.equal(canRetryDelivery(make({ delivery_status: 'FAILED' })), true)
  assert.equal(canRetryDelivery(make({ delivery_status: 'PENDING' })), true)
  // 이미 보냈으면 재발송은 중복 발송이다.
  assert.equal(canRetryDelivery(make({ delivery_status: 'SENT' })), false)
  // 리포트가 없으면 보낼 것이 없다 — 버튼을 보여주면 눌러도 409만 받는다.
  assert.equal(
    canRetryDelivery(make({ report_status: 'BLOCKED', delivery_status: 'FAILED' })),
    false,
  )
})

test('an already released lock is not offered again', () => {
  assert.equal(canReleaseLock(make()), true)
  assert.equal(canReleaseLock(make({ lock_released_at: '2026-07-30T00:00:00Z' })), false)
})

// ── 한 줄 안내 ───────────────────────────────────────────────────────
test('the hint names the action for each terminal failure', () => {
  assert.match(diagnosisHint(make({ execution_status: 'FAILED' })), /측정이/)
  assert.match(diagnosisHint(make({ report_status: 'BLOCKED' })), /리포트 생성/)
  assert.match(diagnosisHint(make({ delivery_status: 'FAILED' })), /재발송/)
})

test('a sent report with partial measurement says so', () => {
  // "발송 완료"만 보여주면 AE가 표본 부족을 모른 채 원장에게 보고한다.
  const hint = diagnosisHint(make({ execution_status: 'PARTIAL' }))
  assert.match(hint, /측정 일부가 실패/)
})

test('purged diagnoses are described as purged, not as failures', () => {
  const hint = diagnosisHint(make({ report_status: 'PURGED', delivery_status: 'PENDING' }))
  assert.match(hint, /파기/)
})

test('in-progress states are not described as problems', () => {
  const hint = diagnosisHint(
    make({ execution_status: 'RUNNING', report_status: 'PENDING', delivery_status: 'PENDING' }),
  )
  assert.match(hint, /진행 중/)
})
