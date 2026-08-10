import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  type LeadDiagnosisSummary,
  canReleaseLock,
  canRetryDelivery,
  diagnosisBadges,
  diagnosisHint,
  recoveryAction,
  leadNeedsAttention,
  needsAttention,
} from './lead-diagnosis-status.ts'

const LEADS_PAGE = readFileSync(new URL('../app/leads/page.tsx', import.meta.url), 'utf8')

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

test('알 수 없는 상태는 영문 원문 대신 확인 필요로 표시한다', () => {
  const badge = diagnosisBadges(make({ delivery_status: 'WAT' }))[2]
  assert.equal(badge.label, '확인 필요')
  assert.doesNotMatch(badge.label, /WAT/)
  assert.equal(badge.tone, 'muted')
})

test('리드 운영 화면은 SLA 대신 인수 처리 기한을 안내한다', () => {
  assert.doesNotMatch(LEADS_PAGE, /\bSLA\b/)
  assert.match(LEADS_PAGE, /담당자·계약·인수 처리 기한 입력/)
})

test('복구 사유 최소 길이는 API 계약과 같은 3자다', () => {
  assert.match(LEADS_PAGE, /reason\.length < 3/)
  assert.match(LEADS_PAGE, /사유를 3자 이상 입력/)
  assert.doesNotMatch(LEADS_PAGE, /사유를 2자 이상 입력/)
})

test('복구 모달은 개발 용어 없이 운영자가 확인한 사실을 적게 한다', () => {
  assert.doesNotMatch(LEADS_PAGE, /공급자 설정|PDF 렌더링/)
  assert.match(LEADS_PAGE, /같은 질문으로 다시 확인이 필요해 재측정/)
  assert.match(LEADS_PAGE, /리포트가 열리지 않아 다시 만들기/)
})

test('복구 모달 실행과 취소 버튼은 44px 조작 영역을 가진다', () => {
  assert.match(LEADS_PAGE, /flex-1 min-h-11 rounded-lg bg-blue-600/)
  assert.match(LEADS_PAGE, /min-h-11 rounded-lg bg-slate-100/)
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

test('a failed measurement exposes one measurement recovery action first', () => {
  const action = recoveryAction(
    make({
      execution_status: 'FAILED',
      report_status: 'BLOCKED',
      delivery_status: 'PENDING',
    }),
  )
  assert.equal(action?.kind, 'remeasure')
  assert.equal(action?.enabled, true)
  assert.equal(
    action?.description,
    '같은 환자 질문을 AI에 다시 물어 병원명이 확인되는지 측정합니다.',
  )
})

test('a blocked report exposes rebuild only after usable measurement', () => {
  const action = recoveryAction(
    make({ execution_status: 'PARTIAL', report_status: 'BLOCKED', delivery_status: 'PENDING' }),
  )
  assert.equal(action?.kind, 'rebuild')
  assert.equal(action?.enabled, true)
  assert.equal(action?.description, '기존 리포트는 보관하고 새 리포트를 만듭니다.')
})

test('an active recovery replaces the button with an authoritative progress outcome', () => {
  const action = recoveryAction(
    make({
      execution_status: 'FAILED',
      report_status: 'BLOCKED',
      recovery_runs: {
        measurement: { id: 'run-1', state: 'RUNNING', requested_at: '2026-08-10T00:00:00Z' },
        report: null,
      },
    }),
  )
  assert.equal(action?.kind, 'progress')
  assert.equal(action?.enabled, false)
  assert.match(action?.label ?? '', /진행/)
})

test('an unsafe sent report rebuild is disabled with an operations-center handoff', () => {
  const action = recoveryAction(
    make({ execution_status: 'SUCCEEDED', report_status: 'BLOCKED', delivery_status: 'SENT' }),
  )
  assert.equal(action?.kind, 'support')
  assert.equal(action?.enabled, false)
  assert.match(action?.description ?? '', /이미 전달/)
})
