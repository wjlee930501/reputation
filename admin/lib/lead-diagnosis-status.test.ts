import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  type LeadDiagnosisSummary,
  canReleaseLock,
  canRetryDelivery,
  diagnosisBadges,
  diagnosisHint,
  diagnosisReportHref,
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
    ['측정', '보고서', '고객 발송'],
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

test('리드 운영 화면은 SLA 대신 우리말 기한 표현을 쓴다', () => {
  assert.doesNotMatch(LEADS_PAGE, /\bSLA\b/)
  assert.match(LEADS_PAGE, /첫 연락 기한/)
})

test('복구 사유 최소 길이는 API 계약과 같은 3자다', () => {
  assert.match(LEADS_PAGE, /reason\.length < 3/)
  assert.match(LEADS_PAGE, /사유를 3자 이상 입력/)
  assert.doesNotMatch(LEADS_PAGE, /사유를 2자 이상 입력/)
})

test('복구 모달은 개발 용어 없이 운영자가 확인한 사실을 적게 한다', () => {
  assert.doesNotMatch(LEADS_PAGE, /공급자 설정|PDF 렌더링/)
  assert.match(LEADS_PAGE, /같은 질문으로 다시 확인이 필요해 재측정/)
  // 재생성은 실패 복구만이 아니다 — 생성 기준이 바뀌어 다시 만드는 경우도 예시로 든다.
  assert.match(LEADS_PAGE, /보고서 생성 기준이 바뀌어 최신 기준으로 다시 만들기/)
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

test('a ready report opens through the authenticated Admin route', () => {
  assert.equal(
    diagnosisReportHref('lead/1', make({ id: 'diagnosis 1' })),
    '/api/admin/leads/lead%2F1/diagnoses/diagnosis%201/report',
  )
  assert.equal(diagnosisReportHref('lead-1', make({ report_status: 'BLOCKED' })), null)
  assert.equal(diagnosisReportHref('lead-1', make({ report_status: 'PURGED' })), null)
  assert.match(
    diagnosisReportHref('lead-1', make({ delivery_status: 'INTERNAL' })) ?? '',
    /\/api\/admin\/leads\/lead-1\/diagnoses\/d1\/report/,
  )
})

test('an already released lock is not offered again', () => {
  assert.equal(canReleaseLock(make()), true)
  assert.equal(canReleaseLock(make({ lock_released_at: '2026-07-30T00:00:00Z' })), false)
})

test('an internal inquiry diagnosis has no customer retry or free-lock action', () => {
  const diagnosis = make({ delivery_status: 'INTERNAL' })
  assert.equal(canRetryDelivery(diagnosis), false)
  assert.equal(canReleaseLock(diagnosis), false)
  assert.match(diagnosisHint(diagnosis), /콜용 보고서/)
})

test('콜용 진단의 한 줄은 상태만 말하고 고객 전달을 말하지 않는다', () => {
  // 이 행은 처음부터 고객에게 나가지 않는다 — 신청자에게 무엇이 갔는지 말하면 AE가 잘못 읽는다.
  const hints = [
    diagnosisHint(make({ delivery_status: 'INTERNAL' })),
    diagnosisHint(make({ delivery_status: 'INTERNAL', report_status: 'PENDING' })),
    diagnosisHint(
      make({ delivery_status: 'INTERNAL', execution_status: 'FAILED', report_status: 'PENDING' }),
    ),
    diagnosisHint(make({ delivery_status: 'INTERNAL', report_status: 'BLOCKED' })),
  ]
  for (const hint of hints) {
    assert.match(hint, /콜용 보고서/)
    assert.doesNotMatch(hint, /신청자|발송|전달/)
  }
  assert.match(hints[2], /만들지 못했습니다/)
  assert.match(hints[3], /만들지 못했습니다/)
})

test('콜용 진단의 개발팀 안내는 고객 발송 이력을 이유로 들지 않는다', () => {
  const action = recoveryAction(
    make({ delivery_status: 'INTERNAL', execution_status: 'FAILED', report_status: 'READY' }),
  )
  assert.equal(action?.kind, 'support')
  assert.match(action?.description ?? '', /콜용 보고서/)
  assert.doesNotMatch(action?.description ?? '', /고객에게 발송|신청자/)
})

test('도입문의 진단의 복구 진행 문구는 고객 전달을 말하지 않는다', () => {
  assert.match(
    LEADS_PAGE,
    /isIntroductionInquiry\(lead\) && diagnosis\.delivery_status === 'INTERNAL'\s*\n?\s*\? '콜용 보고서가 아직 없습니다/,
  )
})

test('고객 영향 문구는 리드 표식과 이 행의 발송 상태를 함께 보고 감춘다', () => {
  // 표식만 보면 표식이 사라진 행에서 「신청자가 … 받지 못합니다」가 콜용 행에 다시 붙는다.
  assert.match(
    LEADS_PAGE,
    /!isIntroductionInquiry\(lead\)\s*\n?\s*&& diagnosis\.delivery_status !== 'INTERNAL' && \(\s*\n\s*<p[^>]*>\s*\n\s*고객 영향: 신청자가 정확한 진단 보고서를 받지 못합니다\./,
  )
})

test('the inquiry row exposes internal generation and explicitly guards retry', () => {
  assert.match(LEADS_PAGE, /진단 생성\(내부용\)/)
  assert.match(LEADS_PAGE, /콜용 \/ 고객 미발송/)
  assert.match(
    LEADS_PAGE,
    /!isIntroductionInquiry\(lead\) && canRetryDelivery\(diagnosis\)/,
  )
})

// ── 한 줄 안내 ───────────────────────────────────────────────────────
test('the hint names the action for each terminal failure', () => {
  assert.match(diagnosisHint(make({ execution_status: 'FAILED' })), /측정이/)
  assert.match(diagnosisHint(make({ report_status: 'BLOCKED' })), /보고서 생성/)
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
  assert.equal(action?.description, '기존 보고서는 보관하고 새 보고서를 만듭니다.')
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
  assert.match(action?.description ?? '', /고객에게 발송/)
})

test('a ready call report can be rebuilt after the generation logic improves', () => {
  // Given: 도입문의로 만들어진 콜용 보고서 — 실패하지 않았고 고객에게 나가지도 않는다
  const action = recoveryAction(
    make({ execution_status: 'SUCCEEDED', report_status: 'READY', delivery_status: 'INTERNAL' }),
  )

  // Then: 생성 로직을 고친 뒤 최신 기준으로 다시 만들 수 있다
  assert.equal(action?.kind, 'rebuild')
  assert.equal(action?.enabled, true)
  assert.match(action?.description ?? '', /그대로 보관하고 최신 기준/)
})

test('a ready report already sent to the applicant offers no rebuild at all', () => {
  // Given: 신청자에게 발송이 끝난 정상 보고서
  const action = recoveryAction(
    make({ execution_status: 'SUCCEEDED', report_status: 'READY', delivery_status: 'SENT' }),
  )

  // Then: 조치할 일이 없으므로 '개발팀 확인 필요'조차 띄우지 않는다.
  // 공개 링크가 최신 버전을 서빙하므로 제자리 재생성은 백엔드도 막는다.
  assert.equal(action, null)
})

test('a ready report still being delivered is left alone', () => {
  assert.equal(
    recoveryAction(
      make({ execution_status: 'SUCCEEDED', report_status: 'READY', delivery_status: 'SENDING' }),
    ),
    null,
  )
})
