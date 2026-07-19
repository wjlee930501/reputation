import assert from 'node:assert/strict'
import test from 'node:test'

import { readReportStrategy } from './report-strategy.ts'

test('readReportStrategy exposes target evidence, gaps, completed work, and next actions', () => {
  const strategy = readReportStrategy({
    strategy: {
      query_targets: [{
        name: '강남 치질 병원 추천',
        sov_pct: 50,
        platform_sov: { chatgpt: 100, gemini: 0 },
        source_backed_count: 1,
        successful_measurement_count: 2,
        competitor_outcomes: [{ name: '경쟁병원', mention_pct: 50 }],
      }],
      exposure_gaps: [{ gap_type: 'LOW_MENTION_SHARE', gap_type_label: '낮은 AI 언급률', severity: 'HIGH', severity_label: '높음' }],
      completed_actions: [{ title: 'FAQ 발행', linked_content_title: '치질 FAQ' }],
      next_month: '2026-08',
      next_month_actions: [{ title: '공식 근거 보강', owner: 'AE', due_month: '2026-08' }],
      compliance_caveat: '의료광고 검수 필요',
    },
  })

  assert.equal(strategy?.queryTargets[0].platformSov.chatgpt, 100)
  assert.equal(strategy?.queryTargets[0].competitorOutcomes[0].mentionPct, 50)
  assert.equal(strategy?.exposureGaps[0].gapTypeLabel, '낮은 AI 언급률')
  assert.equal(strategy?.completedActions[0].linkedContentTitle, '치질 FAQ')
  assert.equal(strategy?.nextMonthActions[0].title, '공식 근거 보강')
  assert.equal(strategy?.complianceCaveat, '의료광고 검수 필요')
})

test('readReportStrategy ignores absent and malformed snapshots safely', () => {
  assert.equal(readReportStrategy(null), null)
  assert.equal(readReportStrategy({ strategy: 'bad' }), null)
})
