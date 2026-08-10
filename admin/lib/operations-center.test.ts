import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDevelopmentSupportSummary,
  canonicalizeOperationsQuery,
  createUserActionKey,
  deriveQueueView,
  enabledPostAction,
  interpretOperationsConflict,
  operationStatusLabel,
  runStateLabel,
  selectCurrentAction,
  shouldAutoRetrySlack,
  shouldPollRun,
  slackStateLabel,
  updateOperationsQuery,
} from './operations-center.ts'
import type { OperationsQueueRow, OperationsRunState } from '../types/index.ts'

function row(
  id: string,
  overrides: Partial<OperationsQueueRow> = {},
): OperationsQueueRow {
  return {
    id,
    queue: 'TODAY',
    customer: { hospital_id: 'hospital-1', name: '서울 바른 병원', admin_path: '/hospitals/1' },
    status: 'PUBLISH_DUE',
    severity: 'MEDIUM',
    impact: '오늘 확인이 필요합니다.',
    owner: null,
    sla_due_at: null,
    sla_state: 'DUE',
    next_action: '콘텐츠를 확인해 주세요.',
    action: { kind: 'REVIEW', label: '확인', method: 'GET', path: '/hospitals/1/content', enabled: true },
    retry: null,
    safe_cause: null,
    history: [],
    slack: null,
    incident_id: null,
    operation_run_id: null,
    content_id: null,
    report_id: null,
    version: null,
    occurred_at: '2026-08-10T00:00:00Z',
    ...overrides,
  }
}

test('canonical query parses supported values and drops unsafe noise', () => {
  // Given
  const source = new URLSearchParams('queue=INCIDENTS&status=open&page=-2&detail=i-1&unknown=x')

  // When
  const canonical = canonicalizeOperationsQuery(source)

  // Then
  assert.equal(canonical.toString(), 'queue=incidents&status=OPEN&detail=i-1')
})

test('changing a quick filter resets page while preserving detail', () => {
  // Given
  const source = new URLSearchParams('queue=today&page=4&detail=content%3A1')

  // When
  const next = updateOperationsQuery(source, { owner: 'AE QA' })

  // Then
  assert.equal(next.toString(), 'queue=today&owner=AE+QA&detail=content%3A1')
})

test('changing queue resets page and closes unrelated detail', () => {
  // Given
  const source = new URLSearchParams('queue=today&page=3&detail=content%3A1&severity=HIGH')

  // When
  const next = updateOperationsQuery(source, { queue: 'incidents' })

  // Then
  assert.equal(next.toString(), 'queue=incidents&severity=HIGH')
})

test('selecting a task in another queue keeps its explicitly supplied detail', () => {
  // Given
  const source = new URLSearchParams('queue=today&detail=content%3A1')

  // When
  const next = updateOperationsQuery(source, { queue: 'incidents', detail: 'incident:2' })

  // Then
  assert.equal(next.toString(), 'queue=incidents&detail=incident%3A2')
})

test('current action prioritizes overdue before critical and Slack failures', () => {
  // Given
  const items = [
    row('slack', { slack: { notification_id: 'n-1', notification_type: 'OPS', state: 'FAILED', attempt_count: 3, max_attempts: 3, next_attempt_at: null, sent_at: null, safe_error_code: 'X', safe_error_message: null, version: 2 } }),
    row('critical', { severity: 'CRITICAL' }),
    row('overdue', { sla_state: 'OVERDUE', severity: 'LOW' }),
  ]

  // When
  const selected = selectCurrentAction(items)

  // Then
  assert.equal(selected?.id, 'overdue')
})

test('run and Slack labels are exhaustive over every backend state', () => {
  // Given
  const runStates: readonly OperationsRunState[] = ['REQUESTED', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED']
  const slackStates = ['PENDING', 'SENDING', 'RETRYING', 'HOLD', 'SENT', 'FAILED'] as const

  // When / Then
  assert.deepEqual(runStates.map(runStateLabel), ['요청 접수', '대기 중', '실행 중', '완료', '일부 완료', '실패', '취소'])
  assert.deepEqual(slackStates.map(slackStateLabel), ['발송 대기', '발송 중', '전송 재시도 대기', '전송 결과 확인 필요', '발송 완료', 'Slack 전달 실패'])
})

test('customer-facing operation labels never expose raw backend states', () => {
  const states = ['ONBOARDING', 'ANALYZING', 'BUILDING', 'PENDING_DOMAIN', 'ACTIVE', 'PAUSED', 'PUBLISH_DUE', 'REVIEW_PENDING', 'OVERDUE_REVIEW', 'MISSING', 'DELIVERY_PENDING', 'OPEN', 'RETRYING', 'RECOVERED', 'ACKNOWLEDGED']

  assert.deepEqual(states.map(operationStatusLabel), [
    '온보딩 진행 중', 'AI 진단 분석 중', '콘텐츠 허브 준비 중', '도메인 확인 대기', '운영 중', '운영 일시 정지',
    '오늘 발행 예정', '발행 후 확인 대기', '발행 후 확인 기한 지남', '지난달 보고서 미생성', '원장 전달 검수 대기',
    '처리 필요', '복구 재시도 중', '복구 확인됨', '확인 완료',
  ])
  assert.equal(operationStatusLabel('UNRECOGNIZED'), '상태 확인 필요')
})

test('developer support copy includes safe facts and excludes task payloads', () => {
  const incident = row('incident:1', {
    queue: 'INCIDENTS', status: 'RETRYING', impact: '오늘 콘텐츠 생성이 늦어지고 있습니다.',
    safe_cause: '연결 작업 응답을 기다리고 있습니다.', occurred_at: '2026-08-10T01:00:00Z',
    history: [{ event: 'OCCURRED', at: '2026-08-10T01:05:00Z' }],
  })
  const summary = buildDevelopmentSupportSummary({
    incident,
    run: {
      run_id: 'run-1', parent_run_id: null, operation_type: 'CONTENT', state: 'RUNNING', attempt_count: 1,
      total_count: 1, success_count: 0, failure_count: 0, skipped_count: 0, safe_error_code: 'SAFE_TIMEOUT',
      safe_error_message: 'safe', requested_at: '2026-08-10T01:00:00Z', queued_at: null, started_at: null,
      completed_at: null, version: 1, retry: null,
    },
  }, 'https://admin.example.test')

  assert.match(summary, /병원: 서울 바른 병원/)
  assert.match(summary, /현상: 복구 재시도 중/)
  assert.match(summary, /오류 식별자\(개발팀용\): SAFE_TIMEOUT/)
  assert.match(summary, /detail=incident%3A1/)
  assert.doesNotMatch(summary, /request_payload|result_summary|task_id|lease_owner/)
})

test('conflict guidance refetches and restores focus to the remaining safe target', () => {
  // Given / When
  const conflict = interpretOperationsConflict({ code: 'INCIDENT_VERSION_CONFLICT', current_version: 4, current_state: 'RECOVERED', refetch_path: '/api/admin/operations/incidents/i-1' })

  // Then
  assert.equal(conflict.refetchPath, '/api/admin/operations/incidents/i-1')
  assert.equal(conflict.focusTarget, 'current-action')
  assert.equal(conflict.currentVersion, 4)
  assert.equal(conflict.message, '다른 운영자가 먼저 변경했습니다. 최신 상태로 갱신했습니다. 다시 확인해 주세요.')
})

test('one user action gets one stable idempotency key', () => {
  // Given / When
  const first = createUserActionKey('RETRY_RUN', 'run-1', 'nonce-1')
  const repeated = createUserActionKey('RETRY_RUN', 'run-1', 'nonce-1')

  // Then
  assert.equal(first, repeated)
  assert.notEqual(first, createUserActionKey('RETRY_RUN', 'run-1', 'nonce-2'))
})

test('server action descriptor controls permission and exact mutation path', () => {
  const descriptor = { kind: 'RETRY_RUN', label: '작업 다시 시도', method: 'POST' as const, path: '/server-owned/retry', enabled: true }
  const disabled = { ...descriptor, enabled: false, path: '/must-not-run' }

  assert.equal(enabledPostAction(descriptor)?.path, '/server-owned/retry')
  assert.equal(enabledPostAction(disabled), null)
  assert.equal(enabledPostAction({ ...descriptor, method: 'GET' }), null)
})

test('Slack HOLD never auto retries and active runs alone poll', () => {
  // Given / When / Then
  assert.equal(shouldAutoRetrySlack('HOLD'), false)
  assert.equal(shouldAutoRetrySlack('RETRYING'), true)
  assert.equal(shouldPollRun('RUNNING'), true)
  assert.equal(shouldPollRun('FAILED'), false)
})

test('queue view distinguishes loading, empty, error and ready', () => {
  // Given / When / Then
  assert.equal(deriveQueueView(null, '', true), 'loading')
  assert.equal(deriveQueueView(null, 'network', false), 'error')
  assert.equal(deriveQueueView({ queue: 'TODAY', total: 0, page: 1, page_size: 25, items: [] }, '', false), 'empty')
  assert.equal(deriveQueueView({ queue: 'TODAY', total: 1, page: 1, page_size: 25, items: [row('a')] }, '', false), 'ready')
})
