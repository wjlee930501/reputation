import assert from 'node:assert/strict'
import test from 'node:test'

import {
  COMPETITOR_REDELIVER_WARNING,
  competitorRefreshIdempotencyKey,
  competitorRefreshIsActive,
  competitorRefreshPlan,
  competitorRefreshProgress,
  parseCompetitorRefreshStatus,
} from './competitor-refresh.ts'

const base = {
  hospital_id: 'h',
  period_year: 2026,
  period_month: 10,
  registered_competitors: ['가나의원', '다라의원'],
  records_total: 60,
  pending_records: 12,
  pending_names: ['다라의원'],
  report_id: 'r1',
  report_version: 3,
  latest_run: null,
}

test('status payload is parsed defensively and plan explains what the button will do', () => {
  const status = parseCompetitorRefreshStatus(base)
  assert.ok(status)
  assert.deepEqual(status.registeredCompetitors, ['가나의원', '다라의원'])
  assert.equal(status.pendingRecords, 12)
  const plan = competitorRefreshPlan(status)
  assert.equal(plan.kind, 'ready')
  assert.match(plan.message, /등록 경쟁 병원 2곳/)
  assert.match(plan.message, /60건 중 12건/)
  assert.match(plan.message, /측정 숫자는 바뀌지 않습니다/)
  assert.equal(parseCompetitorRefreshStatus('nope'), null)
})

test('without registered competitors the plan sends the operator to the hospital info tab', () => {
  const status = parseCompetitorRefreshStatus({ ...base, registered_competitors: [] })
  assert.ok(status)
  const plan = competitorRefreshPlan(status)
  assert.equal(plan.kind, 'register')
  assert.match(plan.message, /병원 정보 탭/)
})

test('an active chain blocks a second request and is reported as progress', () => {
  const running = parseCompetitorRefreshStatus({
    ...base,
    latest_run: { run_id: 'run-1', state: 'RUNNING', stage: 'RUNNING' },
  })
  assert.ok(running)
  assert.equal(competitorRefreshIsActive(running.latestRun), true)
  assert.equal(competitorRefreshPlan(running).kind, 'running')
  assert.equal(competitorRefreshProgress(running.latestRun)?.tone, 'progress')

  const rebuilding = parseCompetitorRefreshStatus({
    ...base,
    latest_run: {
      run_id: 'run-1', state: 'SUCCEEDED', stage: 'REPORT_REFRESH_QUEUED',
      judged_records: 10, unresolved_records: 2, report_run: { run_id: 'run-2', state: 'QUEUED' },
    },
  })
  assert.ok(rebuilding)
  assert.equal(competitorRefreshIsActive(rebuilding.latestRun), true)
  const progress = competitorRefreshProgress(rebuilding.latestRun)
  assert.equal(progress?.label, '경쟁 현황을 다시 만드는 중')
  assert.match(progress?.detail ?? '', /판정 보충 10건 · 미확정 2건/)
})

test('a finished chain reports counts, the new version and the redeliver warning', () => {
  const done = parseCompetitorRefreshStatus({
    ...base,
    latest_run: {
      run_id: 'run-1', state: 'SUCCEEDED', stage: 'REPORT_REFRESH_QUEUED',
      judged_records: 12, unresolved_records: 0,
      report_run: { run_id: 'run-2', state: 'SUCCEEDED', stage: 'ARTIFACT_VALIDATED', report_version: 4, report_id: 'r2' },
    },
  })
  assert.ok(done)
  assert.equal(competitorRefreshIsActive(done.latestRun), false)
  const progress = competitorRefreshProgress(done.latestRun)
  assert.equal(progress?.tone, 'done')
  assert.match(progress?.detail ?? '', /v4/)
  assert.ok(progress?.detail.includes(COMPETITOR_REDELIVER_WARNING))
  assert.doesNotMatch(JSON.stringify(progress), /SUCCEEDED|ARTIFACT_VALIDATED|REJUDGE/)
})

test('failures are told apart: cost hold, judgment failure, refused new version', () => {
  const cost = competitorRefreshProgress({
    runId: 'r', state: 'PARTIAL', stage: 'COST_BLOCKED', safeErrorCode: 'COMPETITOR_REJUDGE_COST_BLOCKED',
    requestedAt: null, completedAt: null, judgedRecords: 0, unresolvedRecords: 12, pendingRecords: 12, costBlocked: true, reportRun: null,
  })
  assert.equal(cost?.label, '비용 한도로 보류됨')
  const failed = competitorRefreshProgress({
    runId: 'r', state: 'FAILED', stage: 'FAILED', safeErrorCode: 'COMPETITOR_REJUDGE_FAILED',
    requestedAt: null, completedAt: null, judgedRecords: null, unresolvedRecords: null, pendingRecords: null, costBlocked: false, reportRun: null,
  })
  assert.equal(failed?.tone, 'problem')
  assert.match(failed?.detail ?? '', /미확정으로 남은 답변만 다시 확인/)
  const refused = competitorRefreshProgress({
    runId: 'r', state: 'SUCCEEDED', stage: 'REPORT_REFRESH_QUEUED', safeErrorCode: null,
    requestedAt: null, completedAt: null, judgedRecords: 12, unresolvedRecords: 0, pendingRecords: 12, costBlocked: false,
    reportRun: { runId: 'c', state: 'FAILED', stage: 'FAILED', reportVersion: null, reportId: null },
  })
  assert.equal(refused?.label, '새 버전을 만들지 못했습니다')
  const blocked = competitorRefreshProgress({
    runId: 'r', state: 'SUCCEEDED', stage: 'REPORT_REFRESH_QUEUED', safeErrorCode: null,
    requestedAt: null, completedAt: null, judgedRecords: 12, unresolvedRecords: 0, pendingRecords: 12, costBlocked: false,
    reportRun: { runId: 'c', state: 'PARTIAL', stage: 'BLOCKED', reportVersion: 5, reportId: 'r5' },
  })
  assert.match(blocked?.label ?? '', /만들어졌지만/)
  assert.match(blocked?.detail ?? '', /v5/)
})

test('idempotency key is safe for the proxy allow-list', () => {
  const key = competitorRefreshIdempotencyKey('report/1', 'abc def')
  assert.match(key, /^[A-Za-z0-9_.:-]{1,255}$/)
  assert.ok(key.startsWith('competitor-refresh:'))
})
