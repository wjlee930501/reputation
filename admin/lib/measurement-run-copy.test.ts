import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  MENTION_RATE_EXCLUSION_COPY,
  MENTION_RATE_FAILURE_ALERT_COPY,
  describeMeasurementRunMentionRateImpact,
} from './measurement-run-copy.ts'

test('a run with both outcomes says the successful measurements still count', () => {
  const text = describeMeasurementRunMentionRateImpact({
    query_count: 150,
    success_count: 140,
    failure_count: 10,
    failure_rate: 6.7,
  })

  assert.match(text, /실패율 6\.7%/)
  assert.match(text, /실패 10건만 분모에서 빠지고/)
  assert.match(text, /성공 140건은 AI 언급률에 반영됩니다/)
})

test('a fully successful run never claims anything was excluded', () => {
  const text = describeMeasurementRunMentionRateImpact({
    query_count: 150,
    success_count: 150,
    failure_count: 0,
    failure_rate: 0,
  })

  assert.match(text, /성공 150건이 모두 AI 언급률에 반영됩니다/)
  assert.doesNotMatch(text, /제외/)
})

test('only a run without a single success is called unreflected', () => {
  const text = describeMeasurementRunMentionRateImpact({
    query_count: 20,
    success_count: 0,
    failure_count: 20,
    failure_rate: 100,
  })

  assert.match(text, /확정된 성공 측정이 없어 이 실행은 AI 언급률에 반영되지 않습니다/)
})

test('ambiguous SUCCESS rows are excluded from the absolute reflected count', () => {
  const text = describeMeasurementRunMentionRateImpact({
    query_count: 150,
    success_count: 140,
    ambiguous_count: 12,
    failure_count: 10,
    failure_rate: 6.7,
  })

  assert.match(text, /판정 미확정 12건은 분모에서 빠지고/)
  assert.match(text, /확정 성공 128건은 AI 언급률에 반영됩니다/)
  assert.doesNotMatch(text, /성공 140건.*반영됩니다/)
})

test('a run with only ambiguous SUCCESS rows has no confirmed mention-rate denominator', () => {
  const text = describeMeasurementRunMentionRateImpact({
    query_count: 8,
    success_count: 8,
    ambiguous_count: 8,
    failure_count: 0,
    failure_rate: 0,
  })

  assert.match(text, /확정된 성공 측정이 없어/)
  assert.doesNotMatch(text, /8건이 모두 AI 언급률에 반영/)
})

test('a run with no measurement attempts reports no failure rate instead of 0%', () => {
  const text = describeMeasurementRunMentionRateImpact({
    query_count: 0,
    success_count: 0,
    failure_count: 0,
    failure_rate: null,
  })

  assert.equal(text, '측정 건이 없어 실패율을 산출할 수 없습니다')
})

test('the failure rate is derived when the backend omits it', () => {
  const text = describeMeasurementRunMentionRateImpact({
    query_count: 4,
    success_count: 3,
    failure_count: 1,
    failure_rate: null,
  })

  assert.match(text, /실패율 25\.0%/)
})

test('the dashboard never tells the operator a whole run leaves the mention rate', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/dashboard/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /describeMeasurementRunMentionRateImpact/)
  assert.match(page, /MENTION_RATE_EXCLUSION_COPY/)
  assert.match(page, /MENTION_RATE_FAILURE_ALERT_COPY/)
  assert.doesNotMatch(page, /AI 언급률 계산에서 제외/)
  assert.doesNotMatch(page, /AI 언급률 계산에는 포함하지 않습니다/)
})

test('the shared copy names both excluded cases, not just failures', () => {
  assert.match(MENTION_RATE_EXCLUSION_COPY, /성공한 측정은 AI 언급률에 그대로 반영/)
  assert.match(MENTION_RATE_EXCLUSION_COPY, /판정이 확정되지 않은 측정/)
  assert.match(MENTION_RATE_FAILURE_ALERT_COPY, /성공한 측정은 언급률에 반영/)
})
