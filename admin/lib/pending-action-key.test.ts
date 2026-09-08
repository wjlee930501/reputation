import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { getOrCreatePendingActionKey } from './pending-action-key.ts'

test('an uncertain POST reuses its key and a confirmed POST can start a new attempt', () => {
  const cache = new Map<string, string>()
  let sequence = 0
  const create = () => `request-${++sequence}`

  const first = getOrCreatePendingActionKey(cache, 'same-payload', create)
  const uncertainRetry = getOrCreatePendingActionKey(cache, 'same-payload', create)
  cache.delete('same-payload')
  const confirmedNextAttempt = getOrCreatePendingActionKey(cache, 'same-payload', create)

  assert.equal(uncertainRetry, first)
  assert.notEqual(confirmedNextAttempt, first)
})

// 콘텐츠 화면에는 재생성 요청이 없다 — 그 조작은 운영 센터가 맡는다.
test('operations and report generation share the pending action key contract', () => {
  const operations = readFileSync(new URL('../app/operations/useOperationsCenter.ts', import.meta.url), 'utf8')
  const reports = readFileSync(new URL('../app/hospitals/[id]/reports/ReportRunStatus.tsx', import.meta.url), 'utf8')

  assert.match(operations, /getOrCreateOperationsMutationKey/)
  assert.match(reports, /getOrCreatePendingActionKey/)
})

test('acknowledged mutations release their key before any follow-up read', () => {
  const operations = readFileSync(new URL('../app/operations/useOperationsCenter.ts', import.meta.url), 'utf8')
  const reports = readFileSync(new URL('../app/hospitals/[id]/reports/ReportRunStatus.tsx', import.meta.url), 'utf8')

  const operationsPost = operations.indexOf('await fetchAPI(mutation.path')
  const operationsRelease = operations.indexOf('mutationKeys.current.delete', operationsPost)
  const operationsRead = operations.indexOf('await loadDetail(selectedRow)', operationsPost)
  assert.ok(operationsPost < operationsRelease && operationsRelease < operationsRead)

  const reportPost = reports.indexOf('await fetchAPI(', reports.indexOf('async function generate'))
  const reportRelease = reports.indexOf('keys.current.delete', reportPost)
  const reportRead = reports.indexOf('await refresh()', reportPost)
  assert.ok(reportPost < reportRelease && reportRelease < reportRead)
})
