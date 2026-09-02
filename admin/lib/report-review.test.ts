import assert from 'node:assert/strict'
import test from 'node:test'

import { parseReport, REPORT_REVIEW_SECTION_ORDER } from './report-review.ts'

const payload = {
  id: 'report-2', hospital_id: 'hospital-1', period_year: 2026, period_month: 7,
  report_type: 'MONTHLY', display: { report_type_label: '월간 리포트', screening_status_label: '검수 대기' },
  has_pdf: true, download_url: '/internal.pdf', created_at: '2026-08-10T00:00:00Z', sent_at: null,
  delivery_ready: true, delivery_blockers: [],
  delivery_warnings: ['약정 콘텐츠 16편 중 15편만 발행되었습니다.', '운영 기준이 리포트 생성 이후 갱신되었습니다 — 필요 시 재생성해 주세요.'],
  doctor_artifact_state: 'VALID',
  doctor_artifact: { state: 'VALID', state_label: '원장 전달용 PDF 검증 완료', sha256: 'a'.repeat(64), page_count: 1, validated_at: '2026-08-10T00:01:00Z' },
  review_evidence: {
    version: 2, version_label: '새 버전 2 · 이전 리포트 보존', supersedes_report_id: 'report-1',
    measurement: { quality: 'COMPLETE', quality_label: '필수 측정 완료', planned_count: 2, success_count: 2, failed_count: 0, excluded_count: 1, problem: '측정 완료', customer_impact: '검토 가능', next_action: '근거 확인' },
    notification: { state: 'NOT_INDIVIDUALLY_LINKED', state_label: '개별 알림 연결 기록 없음', problem: '여러 병원을 묶은 요약 알림입니다.', customer_impact: '발송 성공을 단정할 수 없습니다.', next_action: '운영 센터에서 확인', sent_at: null, operations_url: '/operations?queue=REPORTS' },
  },
  sov_summary: {
    sov_pct: 50,
    platforms: [{ platform: 'chatgpt', platform_label: 'ChatGPT', answer_models: ['gpt-answer-2026-08'], model_observation_complete: true, search_observed_count: 2, search_used_count: 2 }],
    cells: [{ query_key: 'q1', query_text: '강남 내과 추천', query_intent_label: '지역·병원 선택 질문', platform_label: 'ChatGPT', state_label: '측정 완료', measured: true, mentioned: true }],
    comparison: { status: 'NON_COMPARABLE', problem: '지난달 기준 없음', customer_impact: '증감을 말할 수 없습니다.', next_action: '현재 수치만 설명하세요.' },
  },
  content_summary: {
    operations: {
      plan_quota: 16,
      published_count: 15,
      shortfall_count: 1,
      scheduled_slot_count: 16,
      scheduled_slot_state_counts: { PUBLISHED: 15, DRAFT: 1 },
      delivery_warnings: ['약정 콘텐츠 16편 중 15편만 발행되었습니다.'],
      post_publish_review: { required_sample_count: 2, reviewed_count: 1, pending_count: 1, overdue_count: 1, cutoff_at: '2026-08-02T09:00:00Z' },
      operator_copy: { label: '콘텐츠 운영 증거', problem: '사후검수 대기', customer_impact: '전달 불가', next_action: '운영 센터 확인' },
    },
    first_measured_mention_cells: [{ classification_label: '이번 달 처음 확인된 언급', meaning: '지난달 기록이 없습니다.', customer_impact: '상승으로 설명할 수 없습니다.', next_action: '다음 달과 비교하세요.', query_text: '강남 내과 추천', platform_label: 'ChatGPT', related_contents: ['내과 진료 안내'] }],
  },
  delivery_history: [], effective_delivery: null,
}

test('strict report boundary preserves frozen cells, version lineage, and honest Slack evidence', () => {
  const report = parseReport(payload)
  assert.equal(report?.review?.versionLabel, '새 버전 2 · 이전 리포트 보존')
  assert.equal(report?.review?.measurement.successCount, 2)
  assert.equal(report?.review?.notification.state, 'NOT_INDIVIDUALLY_LINKED')
  assert.equal(report?.cells[0]?.stateLabel, '측정 완료')
  assert.deepEqual(report?.platforms[0]?.answerModels, ['gpt-answer-2026-08'])
  assert.equal(report?.platforms[0]?.searchUsedCount, 2)
  assert.equal(report?.mentions[0]?.label, '이번 달 처음 확인된 언급')
  assert.equal(report?.contentOperations?.planQuota, 16)
  assert.equal(report?.contentOperations?.pendingReviewCount, 1)
  assert.equal(report?.contentOperations?.scheduledSlotStateCounts.DRAFT, 1)
  assert.equal(report?.contentOperations?.deliveryWarnings.length, 1)
  // 리포트 전체 경고(콘텐츠 운영 경고 + 운영 기준 버전 갱신 등)는 delivery_ready를 그대로
  // true로 두면서 별도로 노출된다 — 전달을 막지 않는다.
  assert.equal(report?.deliveryReady, true)
  assert.equal(report?.deliveryWarnings.length, 2)
  assert.ok(report?.deliveryWarnings.some((warning) => warning.includes('갱신되었습니다')))
  assert.doesNotMatch(JSON.stringify(report), /SLA|CUSTOMER_READY|raw_response/)
})

test('evidence sections are rendered before every delivery control', () => {
  const deliveryIndex = REPORT_REVIEW_SECTION_ORDER.indexOf('delivery')
  assert.ok(REPORT_REVIEW_SECTION_ORDER.indexOf('measurement') < deliveryIndex)
  assert.ok(REPORT_REVIEW_SECTION_ORDER.indexOf('operations') < deliveryIndex)
  assert.ok(REPORT_REVIEW_SECTION_ORDER.indexOf('artifact') < deliveryIndex)
  assert.ok(REPORT_REVIEW_SECTION_ORDER.indexOf('notification') < deliveryIndex)
})

test('malformed report payload fails closed', () => {
  assert.equal(parseReport({ id: 'report-without-hospital' }), null)
  const report = parseReport({ ...payload, review_evidence: null, delivery_ready: false })
  assert.equal(report?.review, null)
  assert.equal(report?.deliveryReady, false)
})
