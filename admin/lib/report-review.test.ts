import assert from 'node:assert/strict'
import test from 'node:test'

import { parseReport, REPORT_REVIEW_SECTION_ORDER } from './report-review.ts'

const payload = {
  id: 'report-2', hospital_id: 'hospital-1', period_year: 2026, period_month: 7,
  report_type: 'MONTHLY', display: { report_type_label: '월간 리포트', screening_status_label: '검수 대기' },
  has_pdf: true, download_url: '/internal.pdf', created_at: '2026-08-10T00:00:00Z', sent_at: null,
  delivery_ready: true, delivery_blockers: [], doctor_artifact_state: 'VALID',
  doctor_artifact: { state: 'VALID', state_label: '원장 전달용 PDF 검증 완료', sha256: 'a'.repeat(64), page_count: 1, validated_at: '2026-08-10T00:01:00Z' },
  review_evidence: {
    version: 2, version_label: '새 버전 2 · 이전 리포트 보존', supersedes_report_id: 'report-1',
    measurement: { quality: 'COMPLETE', quality_label: '필수 측정 완료', planned_count: 2, success_count: 2, failed_count: 0, excluded_count: 1, problem: '측정 완료', customer_impact: '검토 가능', next_action: '근거 확인' },
    notification: { state: 'NOT_INDIVIDUALLY_LINKED', state_label: '개별 알림 연결 기록 없음', problem: '여러 병원을 묶은 요약 알림입니다.', customer_impact: '발송 성공을 단정할 수 없습니다.', next_action: '운영 센터에서 확인', sent_at: null, operations_url: '/operations?queue=REPORTS' },
  },
  sov_summary: {
    sov_pct: 50,
    cells: [{ query_key: 'q1', query_text: '강남 내과 추천', query_intent_label: '지역·병원 선택 질문', platform_label: 'ChatGPT', state_label: '측정 완료', measured: true, mentioned: true }],
    comparison: { status: 'NON_COMPARABLE', problem: '지난달 기준 없음', customer_impact: '증감을 말할 수 없습니다.', next_action: '현재 수치만 설명하세요.' },
  },
  content_summary: { first_measured_mention_cells: [{ classification_label: '이번 달 처음 확인된 언급', meaning: '지난달 기록이 없습니다.', customer_impact: '상승으로 설명할 수 없습니다.', next_action: '다음 달과 비교하세요.', query_text: '강남 내과 추천', platform_label: 'ChatGPT', related_contents: ['내과 진료 안내'] }] },
  delivery_history: [], effective_delivery: null,
}

test('strict report boundary preserves frozen cells, version lineage, and honest Slack evidence', () => {
  const report = parseReport(payload)
  assert.equal(report?.review?.versionLabel, '새 버전 2 · 이전 리포트 보존')
  assert.equal(report?.review?.measurement.successCount, 2)
  assert.equal(report?.review?.notification.state, 'NOT_INDIVIDUALLY_LINKED')
  assert.equal(report?.cells[0]?.stateLabel, '측정 완료')
  assert.equal(report?.mentions[0]?.label, '이번 달 처음 확인된 언급')
  assert.doesNotMatch(JSON.stringify(report), /SLA|CUSTOMER_READY|raw_response/)
})

test('evidence sections are rendered before every delivery control', () => {
  const deliveryIndex = REPORT_REVIEW_SECTION_ORDER.indexOf('delivery')
  assert.ok(REPORT_REVIEW_SECTION_ORDER.indexOf('measurement') < deliveryIndex)
  assert.ok(REPORT_REVIEW_SECTION_ORDER.indexOf('artifact') < deliveryIndex)
  assert.ok(REPORT_REVIEW_SECTION_ORDER.indexOf('notification') < deliveryIndex)
})

test('malformed report payload fails closed', () => {
  assert.equal(parseReport({ id: 'report-without-hospital' }), null)
  const report = parseReport({ ...payload, review_evidence: null, delivery_ready: false })
  assert.equal(report?.review, null)
  assert.equal(report?.deliveryReady, false)
})
