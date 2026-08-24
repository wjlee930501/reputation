import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  SAFE_CAUSE_CODE_MESSAGES,
  buildDevelopmentSupportSummary,
  canonicalizeOperationsQuery,
  createUserActionKey,
  deriveQueueView,
  describeOperationsDeadline,
  operationsRowTitle,
  enabledPostAction,
  effectiveSafeCause,
  interpretOperationsConflict,
  operationStatusLabel,
  primaryOperationsMutation,
  readOperationsQuery,
  runStateLabel,
  safeCauseText,
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
    cause_code: null,
    cause_message: null,
    cause_group_key: null,
    same_type_count: 1,
    affected_hospital_count: 0,
    cost_guard_category: null,
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

test('recovered incidents use a separate canonical view and reset paging', () => {
  const source = new URLSearchParams('queue=incidents&page=3')

  const next = updateOperationsQuery(source, { recovery: 'confirmed' })

  assert.equal(next.toString(), 'queue=incidents&recovery=confirmed')
  assert.equal(readOperationsQuery(next).recovery, 'confirmed')
})

test('code-like causes and contact details never render as marketer explanations', () => {
  const fallback = '원인 설명을 확인할 수 없습니다.'

  // 우리가 모르는 식별자는 그대로 노출하지 않는다.
  assert.match(safeCauseText('SOME_UNMAPPED_INTERNAL_CODE'), new RegExp(fallback))
  assert.match(safeCauseText('failed for owner@example.test'), new RegExp(fallback))
  assert.match(safeCauseText('redis://private-host:6379'), new RegExp(fallback))
  assert.match(safeCauseText('KeyError: Connection refused'), new RegExp(fallback))
  assert.match(safeCauseText('API_KEY secret-token'), new RegExp(fallback))
  assert.match(safeCauseText('담당자 010-1234-5678에게 연락'), new RegExp(fallback))
  assert.equal(safeCauseText('대표 이미지 생성 연결이 잠시 중단되었습니다.'), '대표 이미지 생성 연결이 잠시 중단되었습니다.')
})

test('generic incident cause falls through to a classified run cause', () => {
  const incident = row('incident:classified', {
    queue: 'INCIDENTS',
    safe_cause: '원인 설명을 확인할 수 없습니다',
  })

  const cause = effectiveSafeCause({
    incident,
    run: {
      run_id: 'run-classified', parent_run_id: null, operation_type: 'V0', state: 'FAILED', attempt_count: 3,
      total_count: 150, success_count: 0, failure_count: 150, skipped_count: 0,
      safe_error_code: 'V0_PROVIDER_UNAVAILABLE', safe_error_message: null,
      requested_at: '2026-08-10T01:00:00Z', queued_at: null, started_at: null,
      completed_at: '2026-08-10T01:10:00Z', version: 1, retry: null,
    },
  })

  assert.equal(cause, '외부 AI 측정 서비스가 응답하지 않거나 일시적으로 제한되었습니다.')
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
    '온보딩 진행 중', 'AI 진단 분석 중', '콘텐츠 허브 준비 중', '공개 주소 확인 대기', '운영 중', '운영 일시 정지',
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
  assert.match(summary, /병원 ID: hospital-1/)
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

test('non-incident POST actions become reasoned idempotent mutations', () => {
  const report = row('report:clinic-1:2026-07', {
    queue: 'REPORTS',
    status: 'MISSING',
    action: {
      kind: 'GENERATE_MONTHLY_REPORT',
      label: '지난달 리포트 생성',
      method: 'POST',
      path: '/hospitals/clinic-1/operations/generate-monthly-report?year=2026&month=7',
      enabled: true,
      reason_required: true,
      requires_idempotency_key: true,
    },
  })

  const mutation = primaryOperationsMutation({ incident: report, run: null }, '월말 누락 복구')

  assert.equal(mutation?.kind, 'POST_ACTION')
  assert.equal(mutation?.label, '지난달 리포트 생성')
  assert.equal(mutation?.path, '/hospitals/clinic-1/operations/generate-monthly-report?year=2026&month=7')
  assert.equal(mutation?.targetId, 'report:clinic-1:2026-07')
  assert.equal(mutation?.requiresIdempotencyKey, true)
})

test('Slack HOLD never auto retries and active runs alone poll', () => {
  // Given / When / Then
  assert.equal(shouldAutoRetrySlack('HOLD'), false)
  assert.equal(shouldAutoRetrySlack('RETRYING'), true)
  assert.equal(shouldPollRun('RUNNING'), true)
  assert.equal(shouldPollRun('FAILED'), false)
})

test('operation detail exposes manual Slack retry for HOLD and FAILED only', () => {
  const source = readFileSync(
    new URL('../app/operations/OperationDetail.tsx', import.meta.url),
    'utf8',
  )

  assert.match(source, /slack\?\.state === 'HOLD' \|\| slack\?\.state === 'FAILED'/)
  assert.match(source, /발송 결과가 불확실해 자동 재시도하지 않습니다/)
  assert.match(source, /이미 전달되었을 가능성이 있으면 재시도하지 마세요/)
})

test('queue view distinguishes loading, empty, error and ready', () => {
  // Given / When / Then
  assert.equal(deriveQueueView(null, '', true), 'loading')
  assert.equal(deriveQueueView(null, 'network', false), 'error')
  assert.equal(deriveQueueView({ queue: 'TODAY', total: 0, page: 1, page_size: 25, items: [] }, '', false), 'empty')
  assert.equal(deriveQueueView({ queue: 'TODAY', total: 1, page: 1, page_size: 25, items: [row('a')] }, '', false), 'ready')
})

test('every error identifier the backend can store has an operator explanation', () => {
  // 서버는 원인을 알고 코드로 저장하는데 화면 목록이 짧아서 "원인 설명을 확인할 수
  // 없습니다"로 덮이던 것이 G-1이다. 백엔드 소스에서 코드를 긁어 빠진 것이 없는지 본다.
  const backendRoot = new URL('../../backend/app/', import.meta.url)
  const files = [
    'workers/generation_incident_control.py',
    'workers/lead_diagnosis_tasks.py',
    'workers/generation_batch_run.py',
    'workers/monthly_slot_incident_control.py',
    'workers/task_incident_control.py',
    'workers/autonomous_recovery.py',
    'workers/tasks.py',
    'services/cost_guard.py',
    'services/domain_health_control.py',
    'services/notification_store.py',
    'services/operation_runs.py',
    'services/site_revalidation_control.py',
    'services/hospital_revalidation_control.py',
    'services/naver_handoff_runs.py',
    'api/admin/content.py',
  ]

  const codes = new Set<string>()
  for (const file of files) {
    const source = readFileSync(new URL(file, backendRoot), 'utf8')
    for (const match of source.matchAll(/safe_error_code(?:=|"\s*:\s*)\s*"([A-Z][A-Z0-9_]+)"/g)) {
      codes.add(match[1])
    }
  }
  // 콘텐츠 생성 사건은 코드→한국어 표를 서버에도 갖고 있다. 그 표의 키도 함께 검사한다.
  const generation = readFileSync(new URL('workers/generation_incident_control.py', backendRoot), 'utf8')
  const causeMap = generation.match(/def _generation_safe_cause[\s\S]*?\}\.get\(/)?.[0] ?? ''
  for (const match of causeMap.matchAll(/"([A-Z][A-Z0-9_]+)":/g)) codes.add(match[1])

  assert.ok(codes.size >= 15, `expected to find backend error codes, found ${codes.size}`)
  const missing = [...codes].filter((code) => !(code in SAFE_CAUSE_CODE_MESSAGES)).sort()
  assert.deepEqual(missing, [], `these codes render as an unknown cause: ${missing.join(', ')}`)
})

test('a known code becomes an explanation, and every explanation reads as Korean prose', () => {
  assert.equal(
    safeCauseText('PROVIDER_TIMEOUT'),
    '콘텐츠 생성 서비스의 응답이 제시간에 오지 않았습니다.',
  )
  for (const [code, message] of Object.entries(SAFE_CAUSE_CODE_MESSAGES)) {
    assert.match(message, /[가-힣]{2,}/, code)
    // 설명이 다시 원인 미상 문구로 걸러지면 안 된다.
    assert.equal(safeCauseText(message), message, code)
  }
})

test('a future deadline is not called imminent, and a passed one says how far past', () => {
  const now = Date.parse('2026-08-23T12:00:00Z')
  const at = (iso: string) => iso.slice(5, 16)

  const far = describeOperationsDeadline(
    { sla_state: 'DUE', sla_due_at: '2026-09-02T12:00:00Z' },
    now,
    at,
  )
  assert.equal(far.tone, 'due')
  assert.doesNotMatch(far.text, /임박|남음/)

  const soon = describeOperationsDeadline(
    { sla_state: 'DUE', sla_due_at: '2026-08-23T15:00:00Z' },
    now,
    at,
  )
  assert.equal(soon.tone, 'due_soon')
  assert.match(soon.text, /3시간 남음/)

  const late = describeOperationsDeadline(
    { sla_state: 'OVERDUE', sla_due_at: '2026-08-21T12:00:00Z' },
    now,
    at,
  )
  assert.equal(late.tone, 'overdue')
  assert.match(late.text, /2일 지남/)
})

test('a row without a deadline says so instead of showing an imminent one', () => {
  const now = Date.parse('2026-08-23T12:00:00Z')
  const none = describeOperationsDeadline({ sla_state: 'NONE', sla_due_at: null }, now, String)
  assert.equal(none.tone, 'none')
  assert.equal(none.text, '처리 기한 없음')

  const broken = describeOperationsDeadline({ sla_state: 'DUE', sla_due_at: 'nope' }, now, String)
  assert.equal(broken.tone, 'none')
  assert.equal(broken.text, '처리 기한 확인 필요')
})

test('two rows for the same hospital have different titles', () => {
  const publishDue = operationsRowTitle(row('content:1', { queue: 'TODAY', status: 'PUBLISH_DUE' }))
  const overdueReview = operationsRowTitle(row('content:2', { queue: 'TODAY', status: 'OVERDUE_REVIEW' }))

  assert.notEqual(publishDue, overdueReview)
  assert.match(publishDue, /오늘 발행 예정/)
  assert.match(overdueReview, /발행 후 확인 기한 지남/)
})

test('an unknown status falls back to the queue name rather than a bare warning', () => {
  const title = operationsRowTitle(row('x', { queue: 'REPORTS', status: 'SOMETHING_NEW' }))

  assert.match(title, /월간 리포트/)
  assert.doesNotMatch(title, /상태 확인 필요/)
})

test('the queue list explains a search-emptied page instead of contradicting the tab count', () => {
  const queue = readFileSync(new URL('../app/operations/OperationsQueue.tsx', import.meta.url), 'utf8')

  assert.match(queue, /searchTerm/)
  assert.match(queue, /이 큐 전체는/)
  assert.match(queue, /operationsRowTitle/)
  assert.match(queue, /describeOperationsDeadline/)
  assert.doesNotMatch(queue, /처리 기한 임박'/)
})

test('the current action is chosen from the queue on screen, not from every queue', () => {
  const page = readFileSync(new URL('../app/operations/page.tsx', import.meta.url), 'utf8')

  assert.match(page, /selectCurrentAction\(center\.visibleItems/)
  assert.doesNotMatch(page, /selectCurrentAction\(center\.overview/)
})

test('the detail panel shows who owns the task and when it is due', () => {
  const detail = readFileSync(new URL('../app/operations/OperationDetail.tsx', import.meta.url), 'utf8')

  assert.match(detail, /담당자 · 처리 기한/)
  assert.match(detail, /row\.owner\?\.name \?\? '미지정'/)
  assert.match(detail, /describeOperationsDeadline/)
})
