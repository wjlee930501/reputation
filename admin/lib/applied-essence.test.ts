import assert from 'node:assert/strict'
import test from 'node:test'

import { appliedEssenceSummary } from './applied-essence.ts'
import type { ContentPhilosophy } from '../types/index.ts'

function philosophy(overrides: Partial<ContentPhilosophy> = {}): ContentPhilosophy {
  return {
    id: 'p1',
    hospital_id: 'h1',
    version: 3,
    status: 'APPROVED',
    positioning_statement: null,
    doctor_voice: null,
    patient_promise: null,
    content_principles: [],
    tone_guidelines: [],
    must_use_messages: [],
    avoid_messages: [],
    treatment_narratives: [],
    local_context: {},
    medical_ad_risk_rules: [],
    evidence_map: {},
    source_asset_ids: ['s1', 's2'],
    unsupported_gaps: [],
    conflict_notes: [],
    synthesis_notes: null,
    source_snapshot_hash: null,
    created_by: 'system:essence-auto',
    reviewed_by: 'system:essence-auto',
    approved_at: '2026-10-14T03:00:00Z',
    approval_note: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

test('자동 요약 판은 입력·제외·뺀 문장·충돌 수를 읽기 전용으로 보인다', () => {
  const rows = appliedEssenceSummary(
    philosophy({
      synthesis_metadata: {
        mode: 'AUTOMATIC',
        input_hashes: { s1: 'a', s2: 'b', s3: 'c' },
        excluded_inputs: [{ source_id: 's4' }],
        dropped_total: 5,
        conflicts: [{ text: 'x' }, { text: 'y' }],
      },
    }),
  )
  const byLabel = Object.fromEntries(rows.map((row) => [row.label, row.value]))
  assert.equal(byLabel['판 번호'], 'v3')
  assert.equal(byLabel['만든 방식'], '자동 요약')
  assert.equal(byLabel['입력 자료'], '3건')
  assert.equal(byLabel['제외한 자료'], '1건')
  assert.equal(byLabel['근거 없어 뺀 문장'], '5건')
  assert.equal(byLabel['충돌을 정리한 항목'], '2건')
  assert.match(byLabel['적용 시각'], /2026/)
})

test('폴백 판과 이전 승인본은 만든 방식을 구분한다', () => {
  const fallback = appliedEssenceSummary(
    philosophy({ synthesis_metadata: { mode: 'AUTOMATIC', fallback: true } }),
  )
  assert.equal(fallback.find((row) => row.label === '만든 방식')?.value, '자동 요약(기본 정보 기반)')
  const legacy = appliedEssenceSummary(philosophy({ synthesis_metadata: {} }))
  assert.equal(legacy.find((row) => row.label === '만든 방식')?.value, '이전 승인본')
  assert.equal(legacy.find((row) => row.label === '입력 자료')?.value, '2건')
  assert.equal(legacy.some((row) => row.label === '근거 없어 뺀 문장'), false)
})
