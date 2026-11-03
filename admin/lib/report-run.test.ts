import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isValidReportRebuildReason,
  getOrCreateReportRequestKey,
  parseReportRuns,
  reportRebuildFingerprint,
  reportRebuildIdempotencyKey,
  reportRunDeveloperNote,
} from './report-run.ts'

test('report runs translate internal states into a three-part Korean action card', () => {
  // Given: a failed backend run with raw internal state names
  const payload: unknown = [{
    run_id: 'run-1',
    parent_run_id: 'run-0',
    state: 'FAILED',
    stage: 'FAILED',
    period_year: 2026,
    period_month: 7,
    report_id: null,
    report_version: null,
    supersedes_report_id: null,
    requested_at: '2026-08-10T00:00:00Z',
    completed_at: '2026-08-10T00:01:00Z',
  }]

  // When: the response crosses the browser boundary
  const [run] = parseReportRuns(payload)

  // Then: operators see a plain-language problem, impact, and exact action
  assert.equal(run?.statusLabel, '보고서를 만들지 못했습니다')
  assert.match(run?.whatHappened ?? '', /만들지\s못/)
  assert.match(run?.customerImpact ?? '', /원장님/)
  assert.equal(
    run?.nextAction,
    '‘보고서 다시 만들기’를 눌러 주세요. 다시 실패하면 ‘개발팀 문의용 정보 복사’를 눌러 개발팀에 전달해 주세요.',
  )
  assert.doesNotMatch(JSON.stringify(run), /SLA|CUSTOMER_READY|PARTIAL/)
  assert.equal(run?.primaryAction, 'rebuild')
  assert.equal(run?.attentionLabel, '조치 필요')
})

test('versioned rebuild names the prior report without exposing raw state', () => {
  // Given: a completed version two rebuild linked to version one
  const payload: unknown = [{
    run_id: 'run-2',
    parent_run_id: 'run-1',
    state: 'SUCCEEDED',
    stage: 'ARTIFACT_VALIDATION_PENDING',
    period_year: 2026,
    period_month: 7,
    report_id: 'report-2',
    report_version: 2,
    supersedes_report_id: 'report-1',
    requested_at: '2026-08-10T00:00:00Z',
    completed_at: '2026-08-10T00:01:00Z',
  }]

  // When: it is parsed
  const [run] = parseReportRuns(payload)

  // Then: the new version and validation action are explicit
  assert.equal(run?.statusLabel, '원장 전달용 PDF 확인이 필요합니다')
  assert.equal(run?.versionLabel, '새 버전 2 · 이전 보고서 보존')
  // 정상 완료도 다시 만들 수 있다 — 생성 로직을 고친 뒤 다시 돌릴 길이 있어야 한다.
  // 다만 먼저 할 일은 검수이므로 주 행동은 그대로 review다.
  assert.equal(run?.canRebuild, true)
  assert.equal(run?.primaryAction, 'review')
  assert.equal(run?.attentionLabel, '검수 필요')
})

test('validated PDF stage stays distinct from final delivery readiness', () => {
  const [run] = parseReportRuns([{
    run_id: 'run-validated', parent_run_id: null, state: 'SUCCEEDED',
    stage: 'ARTIFACT_VALIDATED', period_year: 2026, period_month: 7,
    report_id: 'report-validated', report_version: 1, supersedes_report_id: null,
    requested_at: '2026-08-10T00:00:00Z', completed_at: '2026-08-10T00:01:00Z',
  }])

  assert.equal(run?.statusLabel, '원장 전달용 PDF 검증 완료')
  assert.equal(run?.customerImpact, '최종 전달 가능 여부는 최신 병원 자료와 공개 상태를 함께 확인해야 합니다.')
  assert.equal(run?.nextAction, '보고서 화면에서 최신 자료와 전달 가능 상태를 확인해 주세요.')
  assert.equal(run?.canRebuild, true)
  assert.doesNotMatch(JSON.stringify(run), /CUSTOMER_READY|SLA/)
})

test('blocked reports lead with the operations center before a rebuild', () => {
  const [run] = parseReportRuns([{
    run_id: 'run-blocked', state: 'PARTIAL', stage: 'BLOCKED', period_year: 2026,
    period_month: 7, report_id: 'report-blocked', report_version: 1,
    supersedes_report_id: null, parent_run_id: null,
    requested_at: '2026-08-10T00:00:00Z', completed_at: '2026-08-10T00:01:00Z',
  }])

  assert.equal(run?.primaryAction, 'operations')
  assert.equal(run?.attentionLabel, '조치 필요')
  assert.equal(run?.canRebuild, true)
  // 차단은 측정 부족만을 뜻하지 않는다 — PDF·콘텐츠 검수 차단도 같은 문구로 안내한다.
  assert.equal(run?.statusLabel, '보고서 전달 준비에 확인이 필요합니다')
  assert.equal(
    run?.whatHappened,
    '보고서의 측정 결과, 원장 전달용 PDF 또는 콘텐츠 검수에서 차단 사유가 확인됐습니다.',
  )
  assert.equal(
    run?.nextAction,
    '운영 센터에서 실제 차단 사유와 자동 복구 상태를 확인해 주세요. 조치가 필요한 항목만 처리한 뒤 보고서를 다시 확인하세요.',
  )
  assert.doesNotMatch(run?.whatHappened ?? '', /다시 만들기|측정이 부족/)
})

test('coverage complete reflects a finalized sample, not a promise of full results', () => {
  const [run] = parseReportRuns([{
    run_id: 'run-final', state: 'SUCCEEDED', stage: 'COVERAGE_COMPLETE', period_year: 2026,
    period_month: 7, report_id: 'report-final', report_version: 1,
    supersedes_report_id: null, parent_run_id: null,
    requested_at: '2026-08-10T00:00:00Z', completed_at: '2026-08-10T00:01:00Z',
  }])

  // 마감된 집계는 LIMITED/UNAVAILABLE 최종일 수 있다 — '모두 모였다'고 과장하지 않는다.
  assert.equal(run?.statusLabel, '측정 집계가 마감됐습니다')
  assert.equal(
    run?.whatHappened,
    '확보한 결과로 측정 집계를 확정했습니다. 제한된 결과나 측정 불가 항목은 보고서에 표시됩니다.',
  )
  assert.doesNotMatch(run?.whatHappened ?? '', /모두 모|완료됐/)
})

test('unknown payload is rejected and developer note contains only safe identifiers', () => {
  // Given/When: malformed API input and one known run
  assert.deepEqual(parseReportRuns({ state: 'FAILED' }), [])
  const [run] = parseReportRuns([{
    run_id: 'run-safe', state: 'FAILED', stage: 'FAILED', period_year: 2026, period_month: 7,
    report_id: null, report_version: null, supersedes_report_id: null,
    parent_run_id: null, requested_at: '2026-08-10T00:00:00Z', completed_at: null,
  }])

  // Then: the clipboard fallback omits free-text errors and personal data
  const note = reportRunDeveloperNote('hospital-safe', run!)
  assert.match(note, /hospital-safe/)
  assert.match(note, /run-safe/)
  assert.doesNotMatch(note, /error|email|phone|SLA/i)
})

test('rebuild reason and request key prevent opaque duplicate rebuilds', () => {
  assert.equal(isValidReportRebuildReason('  '), false)
  assert.equal(isValidReportRebuildReason('자료 반영'), true)
  assert.equal(isValidReportRebuildReason('가'.repeat(201)), false)
  assert.equal(
    reportRebuildIdempotencyKey('run-1', 'request-1'),
    reportRebuildIdempotencyKey('run-1', 'request-1'),
  )
  const cache = new Map<string, string>()
  let sequence = 0
  const firstAction = reportRebuildFingerprint('run-1', 2026, 7, ' 늦은 자료 반영 ')
  const firstKey = getOrCreateReportRequestKey(cache, firstAction, () => `key-${++sequence}`)
  const retryKey = getOrCreateReportRequestKey(cache, firstAction, () => `key-${++sequence}`)
  const changedAction = reportRebuildFingerprint('run-1', 2026, 7, '다른 자료 반영')
  const changedKey = getOrCreateReportRequestKey(cache, changedAction, () => `key-${++sequence}`)
  assert.equal(retryKey, firstKey)
  assert.notEqual(changedKey, firstKey)
})
