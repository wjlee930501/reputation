import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { ADMIN_COPY } from './admin-copy.ts'
import {
  AE_CARD_NAMES_PATH,
  AE_DELETE,
  aeEditFingerprint,
  aeEditIdempotencyKey,
  aeEditRunOutcome,
  buildAeEditFields,
  changedCountBySection,
  displayValue,
  draftFromItems,
  groupAeEditItems,
  hasAeEditChanges,
  isDeleteMarker,
  itemIsChanged,
  itemMatchesQuery,
  numbersDiffer,
  parseAeEditPreview,
  problemsByPath,
  problemsFromRejection,
  recountedCard,
  submitBlockedBy,
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
}

const item = (path: string, extra: Record<string, unknown> = {}) => ({
  path, section: 'p1', kind: path.replace(/\[[^\]]*\]/g, ''), type: 'text', value: '값', original: '값', deleted: false,
  row: null, row_deleted: false, context: '', deletable: true, row_deletable: true, overridden: false, numbers_changed: false, ...extra,
})

const items = [
  item('summary.title', { original: '2026년 10월 AI 노출 성과 보고서', value: '2026년 10월 AI 노출 성과 보고서' }),
  item('summary.key_lines[0]', { original: '첫 줄', value: '사람이 고친 첫 줄', overridden: true, row: 'summary.key_lines[0]', context: '1' }),
  item('summary.key_labels[0]', { original: '잘된 점', value: '잘된 점', type: 'label', row: 'summary.key_lines[0]', context: '1' }),
  item('summary.notice', { original: '측정 방식이 바뀌어 지난달과 직접 비교하지 않았습니다.', value: '측정 방식이 바뀌어 지난달과 직접 비교하지 않았습니다.', deleted: true, overridden: true }),
  item('exposure.platform_bars[ChatGPT].value', { section: 'p3', type: 'pct_opt', original: 33.33, value: 33.33, row: 'exposure.platform_bars[ChatGPT]', context: 'ChatGPT' }),
  item('exposure.platform_bars[ChatGPT].detail', { section: 'p3', type: 'label', original: 'AI 답변 12건 중 4건', value: 'AI 답변 12건 중 4건', row: 'exposure.platform_bars[ChatGPT]', context: 'ChatGPT' }),
  item('appendix.caveat_lines[1]', { section: 'appendix', original: '순위나 환자 수 증가를 약속하지 않습니다.', value: '순위나 환자 수 증가를 약속하지 않습니다.', context: '2' }),
  item('content.rows[위내시경 전 준비].title', { section: 'p2', original: '위내시경 전 준비', value: '위내시경 전 준비', row: 'content.rows[위내시경 전 준비]', row_deleted: true, context: '위내시경 전 준비' }),
  item('content.rows[위내시경 전 준비].keyword', { section: 'p2', type: 'label', original: '일반', value: '일반', row: 'content.rows[위내시경 전 준비]', row_deleted: true, context: '위내시경 전 준비' }),
  item(AE_CARD_NAMES_PATH, { type: 'list', original: ['심장초음파', '위내시경'], value: ['심장초음파', '위내시경'], row: 'summary.cards[3]', context: '4', row_deletable: false }),
  item('plan.rows[가상동 위내시경 가능한 병원].action', { section: 'p6', original: '원래 실행 내용', value: '옛 실행 내용', overridden: true, row: 'plan.rows[가상동 위내시경 가능한 병원]', context: '가상동 위내시경 가능한 병원' }),
]

const payload = {
  report_id: 'r1',
  report_version: 2,
  template_version: 'v4',
  editable: summary,
  current: { ...summary, key_lines: ['사람이 고친 첫 줄', '둘째 줄', summary.key_lines[2]] },
  items,
  overrides: { 'summary.key_lines[0]': '사람이 고친 첫 줄', 'summary.notice': { deleted: true }, 'content.rows[위내시경 전 준비]': { deleted: true }, 'plan.action[가상동 위내시경 가능한 병원]': '옛 실행 내용' },
  preview: null,
  problems: [],
  numbers_preserved: true,
  fact_changes: {},
  number_changes: [],
  candidates: { found: [{ record_id: 'rec-1', keyword: 'k', platform: 'Gemini', measured_on: '2026-10-01', excerpt: 'e', insight: 'i' }], missing: [] },
  edited_by: 'ae@example.invalid',
  edited_at: '2026-11-05T01:00:00+00:00',
  dropped: { fields: { 'cases.found.insight': '옛 문장' }, reason: 'NUMBERS_CHANGED' },
  redelivery_required: true,
  redelivery_note: '새 버전은 다시 전달해야 원장님께 반영됩니다.',
}

test('preview parser keeps items, original, current, candidates and dropped paths apart', () => {
  const preview = parseAeEditPreview(payload)
  assert.ok(preview)
  assert.equal(preview.reportId, 'r1')
  assert.equal(preview.items.length, items.length)
  assert.equal(preview.items[3]?.deleted, true)
  assert.equal(preview.items[7]?.rowDeleted, true)
  assert.equal(preview.items[4]?.type, 'pct_opt')
  assert.equal(preview.original.keyLines[0], '첫 줄')
  assert.equal(preview.current.keyLines[0], '사람이 고친 첫 줄')
  assert.equal(preview.candidates.found[0]?.recordId, 'rec-1')
  assert.deepEqual(preview.droppedPaths, ['cases.found.insight'])
  assert.equal(preview.editedBy, 'ae@example.invalid')
  assert.equal(parseAeEditPreview({ report_id: 'x' }), null)
  assert.equal(parseAeEditPreview('nope'), null)
})

test('the draft starts from the stored state and sends only what the user actually changed', () => {
  const preview = parseAeEditPreview(payload)!
  const draft = draftFromItems(preview.items)
  assert.equal(draft.values['summary.key_lines[0]'], '사람이 고친 첫 줄')
  assert.ok(isDeleteMarker(draft.values['summary.notice']))
  assert.equal(draft.rows['content.rows[위내시경 전 준비]'], true)
  assert.deepEqual(draft.caseRecords, { found: 'keep', missing: 'keep' })
  // 아무것도 안 바꿨다: 저장된 수정·지움은 서버가 그대로 잇는다 — 다시 보내지 않는다.
  assert.deepEqual(buildAeEditFields(preview.items, draft, preview.overrides), {})
  // 저장된 수정이 있는 칸을 원문으로 되돌리면 수정 취소(null); 지운 문장을 되살려도 null; 지운 행을 되살려도 null.
  const reverted = buildAeEditFields(preview.items, {
    ...draft,
    values: { ...draft.values, 'summary.key_lines[0]': '첫 줄', 'summary.notice': '측정 방식이 바뀌어 지난달과 직접 비교하지 않았습니다.' },
    rows: { ...draft.rows, 'content.rows[위내시경 전 준비]': false },
  }, preview.overrides)
  assert.deepEqual(reverted, { 'summary.key_lines[0]': null, 'summary.notice': null, 'content.rows[위내시경 전 준비]': null })
  // 옛 별칭(plan.action[K])으로 저장된 수정을 '원래대로' 돌리면 새 경로로 null을 보낸다(Codex 2-P1-1).
  assert.equal(preview.overrides['plan.rows[가상동 위내시경 가능한 병원].action'], '옛 실행 내용')
  const aliasReverted = buildAeEditFields(preview.items, { ...draft, values: { ...draft.values, 'plan.rows[가상동 위내시경 가능한 병원].action': '원래 실행 내용' } }, preview.overrides)
  assert.deepEqual(aliasReverted, { 'plan.rows[가상동 위내시경 가능한 병원].action': null })
  // 지우기·숫자·목록·행 지우기·사례 교체.
  const edited = buildAeEditFields(preview.items, {
    ...draft,
    values: {
      ...draft.values,
      'summary.title': AE_DELETE,
      'appendix.caveat_lines[1]': AE_DELETE,
      'exposure.platform_bars[ChatGPT].value': '40%',
      'exposure.platform_bars[ChatGPT].detail': ' AI 답변 10건 중 4건 ',
      [AE_CARD_NAMES_PATH]: '심장초음파, 위내시경, ',
    },
    rows: { ...draft.rows, 'exposure.platform_bars[ChatGPT]': true, 'summary.cards[3]': true },  // 카드는 행 지우기가 없다 — 보내지 않는다
    caseRecords: { found: 'rec-1', missing: 'keep' },
  }, preview.overrides)
  assert.deepEqual(edited, {
    'summary.title': { deleted: true },
    'appendix.caveat_lines[1]': { deleted: true },
    'exposure.platform_bars[ChatGPT]': { deleted: true },
    'exposure.platform_bars[ChatGPT].value': 40,
    'exposure.platform_bars[ChatGPT].detail': 'AI 답변 10건 중 4건',
    'cases.found.record_id': 'rec-1',
  })
  // 같은 값(쉼표 목록이 원문과 같다)은 보내지 않는다. 사례 되돌리기는 저장된 교체가 있을 때만 null.
  assert.ok(hasAeEditChanges(edited))
  assert.ok(!hasAeEditChanges({}))
  assert.deepEqual(buildAeEditFields(preview.items, { ...draft, caseRecords: { found: 'revert', missing: 'keep' } }, preview.overrides), {})
  const swapped = parseAeEditPreview({ ...payload, overrides: { ...payload.overrides, 'cases.found.record_id': 'rec-9' } })!
  assert.deepEqual(buildAeEditFields(swapped.items, { ...draftFromItems(swapped.items), caseRecords: { found: 'revert', missing: 'keep' } }, swapped.overrides), { 'cases.found.record_id': null })
})

test('numbers differ from the measurement only when the value states another number', () => {
  const pct = { type: 'pct_opt' as const, original: 33.33 }
  assert.equal(numbersDiffer(pct, '33.33'), false)
  assert.equal(numbersDiffer(pct, '40'), true)
  assert.equal(numbersDiffer(pct, ''), true)
  assert.equal(numbersDiffer(pct, AE_DELETE), false)
  const sentence = { type: 'text' as const, original: '측정 키워드 15개 중 4개에서 노출됐습니다.' }
  assert.equal(numbersDiffer(sentence, '측정 키워드 15개 중 4개에서 노출됐습니다!'), false)
  assert.equal(numbersDiffer(sentence, '측정 키워드에서 노출됐습니다.'), false) // 숫자를 뺀 문장은 '다름'이 아니다
  assert.equal(numbersDiffer(sentence, '측정 키워드 15개 중 5개에서 노출됐습니다.'), true)
  assert.equal(numbersDiffer({ type: 'label', original: '실패 61회' }, '실패 60회'), true)
  assert.equal(displayValue({ type: 'list' }, ['a', 'b']), 'a, b')
  assert.equal(displayValue({ type: 'text' }, AE_DELETE), '')
})

test('items group by section and row in report order', () => {
  const preview = parseAeEditPreview(payload)!
  const sections = groupAeEditItems(preview.items)
  assert.deepEqual(sections.map((section) => section.section), ['p1', 'p3', 'appendix', 'p2', 'p1', 'p6'])
  const p1 = sections[0]
  assert.equal(p1.groups[0].key, 'summary.title')
  assert.equal(p1.groups[1].row, 'summary.key_lines[0]')
  assert.equal(p1.groups[1].items.length, 2)
  assert.equal(sections[1].groups[0].items.length, 2)
  assert.equal(sections[3].groups[0].context, '위내시경 전 준비')
})

test('a label change that regroups the exposed treatments shows the recounted card next to the names input', () => {
  const preview = parseAeEditPreview(payload)!
  assert.equal(recountedCard(preview.current, null), null)
  assert.equal(recountedCard(preview.current, preview.current), null)
  const merged = { ...preview.current, cardValue: '1개 항목', cardNames: ['심장초음파'] }
  assert.deepEqual(recountedCard(preview.current, merged), { value: '1개 항목', names: ['심장초음파'], tail: null })
  const panel = readFileSync(new URL('../app/hospitals/[id]/reports/ReportAeEditPanel.tsx', import.meta.url), 'utf8')
  assert.match(panel, /recountedCard\(preview\.current, validated\.summary\)/)
  assert.match(panel, /ADMIN_COPY\.aeEditCardRecounted/)
})

test('a finished run counts as done only when the report version rose past the edited one', () => {
  assert.equal(aeEditRunOutcome(undefined, 2), 'running')
  assert.equal(aeEditRunOutcome({ isActive: true, failed: false, reportVersion: 2 }, 2), 'running')
  assert.equal(aeEditRunOutcome({ isActive: false, failed: false, reportVersion: 2 }, 2), 'failed')
  assert.equal(aeEditRunOutcome({ isActive: false, failed: false, reportVersion: null }, 2), 'failed')
  assert.equal(aeEditRunOutcome({ isActive: false, failed: false, reportVersion: 3 }, 2), 'done')
  assert.equal(aeEditRunOutcome({ isActive: false, failed: true, reportVersion: 3 }, 2), 'failed')
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
    { path: 'competition.replaced[k].competitors[0]', code: 'TOO_LONG', message: '병원 이름 길이' },
    { path: 'p5', code: 'SECTION_EMPTY', message: '빈 절' },
    { path: '', code: 'PDF', message: 'pdf' },
  ], new Set(['summary.key_lines[0]', 'competition.replaced[k].competitors']))
  assert.deepEqual(grouped['summary.key_lines[0]'], ['금지 표현'])
  assert.deepEqual(grouped[AE_CARD_NAMES_PATH], ['길이'])
  assert.deepEqual(grouped['competition.replaced[k].competitors'], ['병원 이름 길이'])
  assert.deepEqual(grouped.p5, ['빈 절'])
  assert.deepEqual(grouped[''], ['pdf'])
  assert.deepEqual(problemsFromRejection({ code: 'AE_EDIT_REJECTED', problems: [{ path: 'p', code: 'EMPTY', message: 'm' }] }), [{ path: 'p', code: 'EMPTY', message: 'm' }])
  assert.deepEqual(problemsFromRejection('x'), [])
})

test('the panel only speaks through ADMIN_COPY and the ae-edit endpoints, offers delete/restore and a PDF preview', () => {
  const panel = readFileSync(new URL('../app/hospitals/[id]/reports/ReportAeEditPanel.tsx', import.meta.url), 'utf8')
  const dialog = readFileSync(new URL('../app/hospitals/[id]/reports/ReportDialog.tsx', import.meta.url), 'utf8')
  assert.match(panel, /reports\/\$\{reportId\}\/ae-edit`/)
  assert.match(panel, /\$\{base\}\/preview`, \{ method: 'POST'/)
  assert.match(panel, /\/preview-pdf`/)
  assert.match(panel, /fetchAPI<\{ operation_run_id\?: string \}>\(base, \{/)
  assert.match(panel, /'Idempotency-Key': key/)
  assert.match(panel, /ADMIN_COPY\.aeEditRedelivery/)
  assert.match(panel, /ADMIN_COPY\.aeEditDelete\b/)
  assert.match(panel, /ADMIN_COPY\.aeEditRevert\b/)
  assert.match(panel, /ADMIN_COPY\.aeEditDeleteRow\b/)
  assert.match(panel, /ADMIN_COPY\.aeEditPreviewPdf\b/)
  assert.match(panel, /ADMIN_COPY\.aeEditNumbersDiffer\b/)
  assert.match(panel, /ADMIN_COPY\.aeEditSections\[/)
  assert.match(panel, /ADMIN_COPY\.aeEditKinds\[/)
  assert.match(panel, /<label htmlFor=\{id\}>/)
  assert.match(panel, /aeEditRunOutcome\(/)
  assert.match(panel, /role="alert"/)
  assert.match(dialog, /<ReportAeEditPanel/)
  assert.match(dialog, /data-report-section="ae-edit"/)
  assert.equal(ADMIN_COPY.aeEditTitle, '보고서 고치기')
  // 화면이 지어낸 한국어 문구가 없다 — JSX 텍스트·placeholder·버튼 글자는 ADMIN_COPY만 읽는다.
  assert.doesNotMatch(panel, /label=\{`[^`]*[가-힣]/)
  assert.doesNotMatch(panel, />[^<{]*[가-힣][^<{]*</)
  assert.doesNotMatch(panel, /placeholder="[^"]*[가-힣]/)
  assert.doesNotMatch(panel, /\? '[가-힣][^']*' : '[가-힣]/)
})

test('search, changed-only counts and the submit-blocked reason follow the draft', () => {
  const preview = parseAeEditPreview(payload)!
  const draft = draftFromItems(preview.items)
  const title = preview.items[0]
  assert.ok(itemMatchesQuery(title, draft.values[title.path], '2026년 10월', '보고서 제목'))
  assert.ok(itemMatchesQuery(title, draft.values[title.path], '보고서제목', '보고서 제목'))
  assert.ok(!itemMatchesQuery(title, draft.values[title.path], '없는 말', '보고서 제목'))
  const fields = buildAeEditFields(preview.items, { ...draft, values: { ...draft.values, 'summary.title': AE_DELETE, 'appendix.caveat_lines[1]': AE_DELETE }, rows: { ...draft.rows, 'exposure.platform_bars[ChatGPT]': true } }, preview.overrides)
  assert.ok(itemIsChanged(title, fields) && !itemIsChanged(preview.items[1], fields))
  assert.deepEqual(changedCountBySection(preview.items, fields), { p1: 1, appendix: 1, p3: 1 })
  assert.equal(submitBlockedBy({ changed: false, validated: false, stale: false, problems: 0, reasonLength: 0 }), 'no_changes')
  assert.equal(submitBlockedBy({ changed: true, validated: false, stale: false, problems: 0, reasonLength: 5 }), 'validate_first')
  assert.equal(submitBlockedBy({ changed: true, validated: false, stale: true, problems: 0, reasonLength: 5 }), 'revalidate')
  assert.equal(submitBlockedBy({ changed: true, validated: true, stale: false, problems: 2, reasonLength: 5 }), 'problems')
  assert.equal(submitBlockedBy({ changed: true, validated: true, stale: false, problems: 0, reasonLength: 2 }), 'reason')
  assert.equal(submitBlockedBy({ changed: true, validated: true, stale: false, problems: 0, reasonLength: 3 }), null)
})

test('revert, delete, row delete and row restore in sequence always send the right fields', () => {
  const preview = parseAeEditPreview(payload)!
  const draft = draftFromItems(preview.items)
  const row = 'exposure.platform_bars[ChatGPT]'
  const valuePath = `${row}.value`
  const steps = [
    { values: { ...draft.values, [valuePath]: '40' }, rows: draft.rows, expect: { [valuePath]: 40 } },
    { values: { ...draft.values, [valuePath]: AE_DELETE }, rows: draft.rows, expect: { [valuePath]: AE_DELETE } },
    { values: { ...draft.values, [valuePath]: 33.33 }, rows: draft.rows, expect: {} }, // 원래대로
    { values: { ...draft.values, [valuePath]: '40' }, rows: { ...draft.rows, [row]: true }, expect: { [row]: AE_DELETE, [valuePath]: 40 } },
    { values: { ...draft.values, [valuePath]: '40' }, rows: { ...draft.rows, [row]: false }, expect: { [valuePath]: 40 } }, // 행 원래대로
    { values: { ...draft.values, [valuePath]: '' }, rows: draft.rows, expect: { [valuePath]: AE_DELETE } }, // 비울 수 있는 수를 비움 = 지움
  ]
  for (const step of steps) assert.deepEqual(buildAeEditFields(preview.items, { ...draft, values: step.values, rows: step.rows }, preview.overrides), step.expect)
  // 원문이 비어 있던 수(측정 없음 '—')는 손대지 않으면 보내지 않는다 — 운영 10월 픽스처에서 '1곳 고침'이 거짓으로 뜨던 버그.
  const blank = parseAeEditPreview({ ...payload, items: [...items, item('exposure.group_rows[A].rate', { section: 'p3', type: 'pct_opt', original: null, value: null, row: 'exposure.group_rows[A]', context: 'A' })] })!
  assert.deepEqual(buildAeEditFields(blank.items, draftFromItems(blank.items), blank.overrides), {})
  const panel = readFileSync(new URL('../app/hospitals/[id]/reports/ReportAeEditPanel.tsx', import.meta.url), 'utf8')
  assert.match(panel, /<iframe title=\{ADMIN_COPY\.aeEditPdfPreviewTitle\} src=\{pdfUrl\}/)
  assert.doesNotMatch(panel, /window\.open\(/)
  assert.match(panel, /window\.confirm\(ADMIN_COPY\.aeEditDiscardConfirm\)/)
  assert.match(panel, /inputMode=\{numeric \? 'decimal' : 'text'\}/)
  assert.match(panel, /sticky bottom-0/)
  assert.match(panel, /data-testid="ae-search"/)
  assert.match(panel, /ADMIN_COPY\.aeEditChangedOnly/)
  assert.match(panel, /data-testid="ae-submit-blocked"/)
})
