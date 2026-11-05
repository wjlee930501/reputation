import { isRecord } from './type-guards.ts'

/**
 * 보고서 AE 수정 판 — 화면이 서버 미리보기(`GET …/reports/{id}/ae-edit/preview`)를 읽고,
 * 입력란 상태를 서버가 받는 `fields`(경로 → 값, null은 수정 취소)로 바꾸는 순수 함수.
 *
 * 경로 문법은 백엔드 `doctor_report_v4.parse_ae_path`와 같다. 화면은 경로를 지어내지 않고
 * 여기서만 만든다 — 서버 화이트리스트 밖 경로는 422로 돌아온다.
 */

export type AeEditCase = {
  readonly label: string
  readonly keyword: string
  readonly excerpt: string
  readonly platform: string
  readonly measuredOn: string
  readonly insight: string
}

export type AeEditPlanRow = {
  readonly priority: number
  readonly keyword: string
  readonly treatment: string
  readonly rateLabel: string
  readonly action: string
  readonly continued: boolean
}

export type AeEditKeywordLabel = {
  readonly keyword: string
  readonly group: string
  readonly treatment: string
  readonly total: string
}

export type AeEditSummary = {
  readonly keyLines: readonly string[]
  readonly cardValue: string
  readonly cardNames: readonly string[]
  readonly cardNamesTail: string | null
  readonly cases: { readonly found: AeEditCase | null; readonly missing: AeEditCase | null }
  readonly planRows: readonly AeEditPlanRow[]
  readonly keywordLabels: readonly AeEditKeywordLabel[]
}

export type AeEditCandidate = {
  readonly recordId: string
  readonly keyword: string
  readonly platform: string
  readonly measuredOn: string
  readonly excerpt: string
  readonly insight: string
}

export type AeEditProblem = { readonly path: string; readonly code: string; readonly message: string }

export type AeEditPreview = {
  readonly reportId: string
  readonly reportVersion: number
  /** 시스템이 만든 원문(수정 전). 입력란의 '원문으로'가 돌아가는 값. */
  readonly original: AeEditSummary
  /** 지금 저장된 버전이 실제로 찍는 값(이전 수정 포함). 입력란의 기본값. */
  readonly current: AeEditSummary
  /** 요청한 수정을 적용한 결과. 요청이 없거나 거절됐으면 null. */
  readonly preview: AeEditSummary | null
  readonly overrides: Readonly<Record<string, unknown>>
  readonly problems: readonly AeEditProblem[]
  readonly numbersPreserved: boolean
  readonly candidates: { readonly found: readonly AeEditCandidate[]; readonly missing: readonly AeEditCandidate[] }
  readonly editedBy: string | null
  readonly editedAt: string | null
  readonly droppedPaths: readonly string[]
  readonly redeliveryNote: string
}

/** 사례 선택 상태: 'keep' = 지금 사례 그대로(저장된 교체가 있으면 그것도 그대로), 'revert' = 시스템 원래 사례로, 아니면 후보 기록 id. */
export type AeEditCaseChoice = 'keep' | 'revert' | string

/**
 * 화면 입력란의 상태 — 값은 '지금 찍히는 값'(저장된 수정 포함) 기준이다. 서버 `fields`로 바꿀 때는 지금 값과
 * 비교해 **실제로 고친 칸만** 보낸다(저장된 수정은 서버가 합쳐 그대로 잇는다). 원문으로 되돌리면 null(수정 취소).
 */
export type AeEditDraft = {
  readonly keyLines: readonly string[]
  readonly cardNames: readonly string[]
  readonly caseInsights: { readonly found: string | null; readonly missing: string | null }
  readonly caseRecords: { readonly found: AeEditCaseChoice; readonly missing: AeEditCaseChoice }
  readonly planActions: Readonly<Record<string, string>>
  readonly treatmentLabels: Readonly<Record<string, string>>
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function parseCase(value: unknown): AeEditCase | null {
  if (!isRecord(value)) return null
  return {
    label: text(value.label),
    keyword: text(value.keyword),
    excerpt: text(value.excerpt),
    platform: text(value.platform),
    measuredOn: text(value.measured_on),
    insight: text(value.insight),
  }
}

function parseSummary(value: unknown): AeEditSummary | null {
  if (!isRecord(value)) return null
  const cases = isRecord(value.cases) ? value.cases : {}
  return {
    keyLines: strings(value.key_lines),
    cardValue: text(value.card_value),
    cardNames: strings(value.card_names),
    cardNamesTail: typeof value.card_names_tail === 'string' ? value.card_names_tail : null,
    cases: { found: parseCase(cases.found), missing: parseCase(cases.missing) },
    planRows: (Array.isArray(value.plan_rows) ? value.plan_rows : []).flatMap((raw) => {
      if (!isRecord(raw)) return []
      return [{
        priority: typeof raw.priority === 'number' ? raw.priority : 0,
        keyword: text(raw.keyword),
        treatment: text(raw.treatment),
        rateLabel: text(raw.rate_label),
        action: text(raw.action),
        continued: raw.continued === true,
      }]
    }),
    keywordLabels: (Array.isArray(value.keyword_labels) ? value.keyword_labels : []).flatMap((raw) => {
      if (!isRecord(raw)) return []
      return [{ keyword: text(raw.keyword), group: text(raw.group), treatment: text(raw.treatment), total: text(raw.total) }]
    }),
  }
}

function parseCandidates(value: unknown): AeEditCandidate[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((raw) => {
    if (!isRecord(raw) || typeof raw.record_id !== 'string') return []
    return [{
      recordId: raw.record_id,
      keyword: text(raw.keyword),
      platform: text(raw.platform),
      measuredOn: text(raw.measured_on),
      excerpt: text(raw.excerpt),
      insight: text(raw.insight),
    }]
  })
}

export function parseAeEditPreview(payload: unknown): AeEditPreview | null {
  if (!isRecord(payload)) return null
  const original = parseSummary(payload.editable)
  if (!original || typeof payload.report_id !== 'string') return null
  const current = parseSummary(payload.current) ?? original
  const candidates = isRecord(payload.candidates) ? payload.candidates : {}
  const dropped = isRecord(payload.dropped) && isRecord(payload.dropped.fields) ? Object.keys(payload.dropped.fields) : []
  return {
    reportId: payload.report_id,
    reportVersion: typeof payload.report_version === 'number' ? payload.report_version : 0,
    original,
    current,
    preview: parseSummary(payload.preview),
    overrides: isRecord(payload.overrides) ? payload.overrides : {},
    problems: (Array.isArray(payload.problems) ? payload.problems : []).flatMap((raw) => {
      if (!isRecord(raw)) return []
      return [{ path: text(raw.path), code: text(raw.code), message: text(raw.message, '검증에 걸렸습니다.') }]
    }),
    numbersPreserved: payload.numbers_preserved === true,
    candidates: { found: parseCandidates(candidates.found), missing: parseCandidates(candidates.missing) },
    editedBy: typeof payload.edited_by === 'string' ? payload.edited_by : null,
    editedAt: typeof payload.edited_at === 'string' ? payload.edited_at : null,
    droppedPaths: dropped,
    redeliveryNote: text(payload.redelivery_note, '새 버전은 다시 전달해야 원장님께 반영됩니다.'),
  }
}

/** 저장된 버전이 찍는 값으로 입력란을 채운다 — 사례 선택은 '지금 사례 유지'에서 시작한다. */
export function draftFromSummary(summary: AeEditSummary): AeEditDraft {
  return {
    keyLines: [...summary.keyLines],
    cardNames: [...summary.cardNames],
    caseInsights: { found: summary.cases.found?.insight ?? null, missing: summary.cases.missing?.insight ?? null },
    caseRecords: { found: 'keep', missing: 'keep' },
    planActions: Object.fromEntries(summary.planRows.map((row) => [row.keyword, row.action])),
    treatmentLabels: Object.fromEntries(summary.keywordLabels.map((row) => [row.keyword, row.treatment])),
  }
}

export const AE_CARD_NAMES_PATH = 'summary.cards[3].names'

/**
 * 라벨 수정으로 노출 진료 항목 묶음이 갈라지거나 합쳐지면 서버가 카드 4의 n·이름·'외 N개'를 노출 행에서 다시 센다.
 * 검증 미리보기(`preview`)의 카드가 지금 찍히는 카드(`current`)와 다를 때만 그 카드를 돌려준다 — 화면은 입력란 옆에
 * "다시 센 카드"로 보여 준다(2026-11-05 운영 미리보기: '4개 항목'이 그대로 남아 노출된 항목이 빠졌다).
 */
export function recountedCard(
  current: AeEditSummary,
  preview: AeEditSummary | null,
): { readonly value: string; readonly names: readonly string[]; readonly tail: string | null } | null {
  if (!preview) return null
  const same = preview.cardValue === current.cardValue && preview.cardNamesTail === current.cardNamesTail && sameList(preview.cardNames, current.cardNames)
  return same ? null : { value: preview.cardValue, names: preview.cardNames, tail: preview.cardNamesTail }
}

export function keyLinePath(index: number): string {
  return `summary.key_lines[${index}]`
}
export function caseInsightPath(slot: 'found' | 'missing'): string {
  return `cases.${slot}.insight`
}
export function caseRecordPath(slot: 'found' | 'missing'): string {
  return `cases.${slot}.record_id`
}
export function planActionPath(keyword: string): string {
  return `plan.action[${keyword}]`
}
export function treatmentLabelPath(keyword: string): string {
  return `treatment_label[${keyword}]`
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index])
}

/**
 * 입력란 상태 → 서버 `fields`. 지금 찍히는 값(`current`)과 같은 칸은 보내지 않는다 — 저장된 수정은 서버가
 * 합쳐 그대로 잇고, 라벨에서 자동 전파된 문장을 명시적 수정으로 되돌려 보내지 않는다(검토 1-P2-1).
 * 바꾼 칸은 값을 보내고, 저장된 수정이 있는 칸을 원문(`original`)으로 되돌렸으면 null(수정 취소)을 보낸다.
 */
export function buildAeEditFields(
  original: AeEditSummary,
  current: AeEditSummary,
  draft: AeEditDraft,
  overrides: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  const fields: Record<string, unknown> = {}
  const put = (path: string, value: unknown, sameAsCurrent: boolean, sameAsOriginal: boolean) => {
    if (sameAsCurrent) return
    fields[path] = sameAsOriginal && path in overrides ? null : value
  }
  draft.keyLines.forEach((line, index) => {
    if (index > 2) return
    const value = line.trim()
    put(keyLinePath(index), value, value === (current.keyLines[index] ?? ''), value === (original.keyLines[index] ?? ''))
  })
  const names = draft.cardNames.map((name) => name.trim()).filter((name) => name.length > 0)
  put(AE_CARD_NAMES_PATH, names, sameList(names, current.cardNames), sameList(names, original.cardNames))
  for (const slot of ['found', 'missing'] as const) {
    const insight = draft.caseInsights[slot]
    const currentCase = current.cases[slot]
    if (currentCase && insight !== null) {
      const value = insight.trim()
      put(caseInsightPath(slot), value, value === currentCase.insight, value === (original.cases[slot]?.insight ?? ''))
    }
    const choice = draft.caseRecords[slot]
    if (choice === 'revert') {
      if (caseRecordPath(slot) in overrides) fields[caseRecordPath(slot)] = null
    } else if (choice !== 'keep') fields[caseRecordPath(slot)] = choice
  }
  for (const row of current.planRows) {
    const action = draft.planActions[row.keyword]
    if (action === undefined) continue
    const value = action.trim()
    const originalRow = original.planRows.find((item) => item.keyword === row.keyword)
    put(planActionPath(row.keyword), value, value === row.action, value === originalRow?.action)
  }
  for (const row of current.keywordLabels) {
    const label = draft.treatmentLabels[row.keyword]
    if (label === undefined) continue
    const value = label.trim()
    const originalRow = original.keywordLabels.find((item) => item.keyword === row.keyword)
    put(treatmentLabelPath(row.keyword), value, value === row.treatment, value === originalRow?.treatment)
  }
  return fields
}

/** 작업 기록에서 이 요청의 결과를 읽는다 — 실패한 작업도 그 달의 기존 보고서 버전을 싣기 때문에 버전이 **올랐는지** 본다(검토 1-P2-3). */
export function aeEditRunOutcome(
  run: { readonly isActive: boolean; readonly failed: boolean; readonly reportVersion: number | null } | undefined,
  versionBefore: number,
): 'running' | 'done' | 'failed' {
  if (!run || run.isActive) return 'running'
  if (run.failed) return 'failed'
  return run.reportVersion !== null && run.reportVersion > versionBefore ? 'done' : 'failed'
}

export function hasAeEditChanges(fields: Readonly<Record<string, unknown>>): boolean {
  return Object.keys(fields).length > 0
}

export function aeEditIdempotencyKey(reportId: string, requestId: string): string {
  return `monthly-report-ae-edit:${reportId}:${requestId}`
}

export function aeEditFingerprint(reportId: string, fields: Readonly<Record<string, unknown>>): string {
  return `${reportId}\u0000${JSON.stringify(Object.keys(fields).sort().map((key) => [key, fields[key]]))}`
}

/** 거절 사유를 칸 옆에 보여 주기 위한 경로 → 메시지 묶음(경로 없는 사유는 ''). */
export function problemsByPath(problems: readonly AeEditProblem[]): Readonly<Record<string, string[]>> {
  const grouped: Record<string, string[]> = {}
  for (const problem of problems) {
    // 카드 이름의 `summary.cards[3].names[2]`는 그 칸 전체에 붙인다.
    const path = problem.path.startsWith(`${AE_CARD_NAMES_PATH}[`) ? AE_CARD_NAMES_PATH : problem.path
    ;(grouped[path] ??= []).push(problem.message)
  }
  return grouped
}

/** 서버 422(`AE_EDIT_REJECTED`) 본문에서 문제 목록을 읽는다. 다른 모양이면 빈 목록. */
export function problemsFromRejection(detail: unknown): AeEditProblem[] {
  if (!isRecord(detail) || !Array.isArray(detail.problems)) return []
  return detail.problems.flatMap((raw) => {
    if (!isRecord(raw)) return []
    return [{ path: text(raw.path), code: text(raw.code), message: text(raw.message, '검증에 걸렸습니다.') }]
  })
}
