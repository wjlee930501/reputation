import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { ADMIN_COPY } from './admin-copy.ts'
import {
  AE_CARD_NAMES_PATH,
  aeEditFingerprint,
  aeEditIdempotencyKey,
  buildAeEditFields,
  draftFromSummary,
  hasAeEditChanges,
  parseAeEditPreview,
  problemsByPath,
  problemsFromRejection,
  treatmentLabelPath,
} from './report-ae-edit.ts'

const summary = {
  key_lines: ['첫 줄', '둘째 줄', "'위내시경' 키워드 대응 콘텐츠 1편을 우선 발행합니다."],
  card_value: '2개 항목',
  card_names: ['심장초음파', '위내시경'],
  card_names_tail: null,
  cases: {
    found: { label: '노출 사례', keyword: '가상동 심장초음파 잘하는 병원', excerpt: '발췌', platform: 'ChatGPT', measured_on: '2026-10-01', insight: '시사점 A' },
    missing: null,
  },
  plan_rows: [{ priority: 1, keyword: '가상동 위내시경 가능한 병원', treatment: '위내시경', rate_label: '0%', action: "'가상동 위내시경' 키워드 대응 콘텐츠 1편 발행 예정", continued: false }],
  keyword_labels: [
    { keyword: '가상동 위내시경 가능한 병원', group: 'B', treatment: '위내시경', total: '0/6' },
    { keyword: '가상동 치료하는 병원', group: 'C', treatment: '치료하', total: '2/6' },
  ],
  content_rows: [],
  treatment_bars: [],
}

const payload = {
  report_id: 'r1',
  report_version: 2,
  template_version: 'v4',
  editable: summary,
  current: { ...summary, key_lines: ['사람이 고친 첫 줄', '둘째 줄', summary.key_lines[2]] },
  overrides: { 'summary.key_lines[0]': '사람이 고친 첫 줄' },
  preview: null,
  problems: [],
  numbers_preserved: true,
  candidates: { found: [{ record_id: 'rec-1', keyword: 'k', platform: 'Gemini', measured_on: '2026-10-01', excerpt: 'e', insight: 'i' }], missing: [] },
  edited_by: 'ae@example.invalid',
  edited_at: '2026-11-05T01:00:00+00:00',
  dropped: { fields: { 'cases.found.insight': '옛 문장' }, reason: 'NUMBERS_CHANGED' },
  redelivery_required: true,
  redelivery_note: '새 버전은 다시 전달해야 원장님께 반영됩니다.',
}

test('preview parser keeps original, current, candidates and dropped paths apart', () => {
  const preview = parseAeEditPreview(payload)
  assert.ok(preview)
  assert.equal(preview.reportId, 'r1')
  assert.equal(preview.original.keyLines[0], '첫 줄')
  assert.equal(preview.current.keyLines[0], '사람이 고친 첫 줄')
  assert.equal(preview.candidates.found[0]?.recordId, 'rec-1')
  assert.deepEqual(preview.droppedPaths, ['cases.found.insight'])
  assert.equal(preview.editedBy, 'ae@example.invalid')
  assert.equal(parseAeEditPreview({ report_id: 'x' }), null)
  assert.equal(parseAeEditPreview('nope'), null)
})

test('draft starts from the current printed values and sends only real differences', () => {
  const preview = parseAeEditPreview(payload)!
  const draft = draftFromSummary(preview.current)
  assert.equal(draft.keyLines[0], '사람이 고친 첫 줄')
  // 아무것도 안 바꿨다: 이전 수정이 있던 첫 줄은 '지금 값'이 원문과 달라 다시 보낸다(서버가 같은 값이면 거른다).
  const unchanged = buildAeEditFields(preview.original, draft, preview.overrides)
  assert.deepEqual(unchanged, { 'summary.key_lines[0]': '사람이 고친 첫 줄' })
  // 원문으로 되돌리면 수정 취소(null)를 보낸다.
  const reverted = buildAeEditFields(preview.original, { ...draft, keyLines: ['첫 줄', '둘째 줄', draft.keyLines[2]] }, preview.overrides)
  assert.deepEqual(reverted, { 'summary.key_lines[0]': null })
  // 라벨 정정·실행 내용·사례 교체·카드 이름.
  const edited = buildAeEditFields(preview.original, {
    ...draft,
    keyLines: ['첫 줄', '둘째 줄', draft.keyLines[2]],
    treatmentLabels: { ...draft.treatmentLabels, '가상동 치료하는 병원': ' 도수치료 ' },
    planActions: { '가상동 위내시경 가능한 병원': "'가상동 위내시경' 키워드 대응 콘텐츠 1편 발행 예정(안내문 보강)" },
    caseRecords: { found: 'rec-1', missing: null },
    cardNames: ['심장초음파', '위내시경', ''],
  }, preview.overrides)
  assert.deepEqual(edited, {
    'summary.key_lines[0]': null,
    [treatmentLabelPath('가상동 치료하는 병원')]: '도수치료',
    'plan.action[가상동 위내시경 가능한 병원]': "'가상동 위내시경' 키워드 대응 콘텐츠 1편 발행 예정(안내문 보강)",
    'cases.found.record_id': 'rec-1',
  })
  assert.ok(hasAeEditChanges(edited))
  assert.ok(!hasAeEditChanges({}))
  assert.equal(AE_CARD_NAMES_PATH, 'summary.cards[3].names')
})

test('request keys and fingerprints are stable for the same field set', () => {
  assert.equal(aeEditIdempotencyKey('r1', 'u1'), 'monthly-report-ae-edit:r1:u1')
  assert.equal(aeEditFingerprint('r1', { b: 1, a: 'x' }), aeEditFingerprint('r1', { a: 'x', b: 1 }))
  assert.notEqual(aeEditFingerprint('r1', { a: 'x' }), aeEditFingerprint('r1', { a: 'y' }))
})

test('problems group by field so the screen can show them next to the input', () => {
  const grouped = problemsByPath([
    { path: 'summary.key_lines[0]', code: 'FORBIDDEN_EXPRESSION', message: '금지 표현' },
    { path: 'summary.cards[3].names[1]', code: 'TOO_LONG', message: '길이' },
    { path: '', code: 'NUMBERS_CHANGED', message: '숫자' },
  ])
  assert.deepEqual(grouped['summary.key_lines[0]'], ['금지 표현'])
  assert.deepEqual(grouped[AE_CARD_NAMES_PATH], ['길이'])
  assert.deepEqual(grouped[''], ['숫자'])
  assert.deepEqual(problemsFromRejection({ code: 'AE_EDIT_REJECTED', problems: [{ path: 'p', code: 'EMPTY', message: 'm' }] }), [{ path: 'p', code: 'EMPTY', message: 'm' }])
  assert.deepEqual(problemsFromRejection('x'), [])
})

test('the panel only speaks through ADMIN_COPY and the ae-edit endpoints, and warns about redelivery', () => {
  const panel = readFileSync(new URL('../app/hospitals/[id]/reports/ReportAeEditPanel.tsx', import.meta.url), 'utf8')
  const dialog = readFileSync(new URL('../app/hospitals/[id]/reports/ReportDialog.tsx', import.meta.url), 'utf8')
  assert.match(panel, /reports\/\$\{reportId\}\/ae-edit`/)
  assert.match(panel, /\$\{base\}\/preview\?overrides=/)
  assert.match(panel, /fetchAPI<\{ operation_run_id\?: string \}>\(base, \{/)
  assert.match(panel, /'Idempotency-Key': key/)
  assert.match(panel, /ADMIN_COPY\.aeEditRedelivery/)
  assert.match(panel, /ADMIN_COPY\.aeEditPreview\b/)
  assert.match(panel, /ADMIN_COPY\.aeEditSubmit\b/)
  assert.match(panel, /role="alert"/)
  assert.match(dialog, /<ReportAeEditPanel/)
  assert.match(dialog, /data-report-section="ae-edit"/)
  assert.equal(ADMIN_COPY.aeEditTitle, '문구·진료 항목 라벨 고치기')
  // 숫자를 고치는 입력란은 없다 — 비율·건수 칸은 읽기 전용 텍스트로만 보인다.
  assert.doesNotMatch(panel, /card_value|rateLabel\}\s*onChange|total\}\s*onChange/)
})
