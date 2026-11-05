import { isRecord } from './type-guards.ts'

/**
 * 보고서 AE 수정 판 — 화면이 서버 미리보기(`GET|POST …/reports/{id}/ae-edit/preview`)를 읽고,
 * 입력란 상태를 서버가 받는 `fields`(경로 → 값, null은 수정 취소, `{deleted: true}`는 지움)로 바꾸는 순수 함수.
 *
 * 원장 PDF에 찍히는 모든 글자가 항목(`items`)으로 온다 — 보고서 순서(01~06·부록)대로, 항목마다 경로·종류·원문·
 * 지금 값·지움 여부·측정값과 다른 숫자. 경로는 서버(`doctor_report_v4.parse_ae_path`)가 만들고 화면은 그대로 돌려보낸다.
 */

export type AeEditItemType = 'text' | 'label' | 'count' | 'pct' | 'pct_opt' | 'int' | 'state' | 'list'

export type AeEditItem = {
  readonly path: string
  /** p1…p6·appendix */
  readonly section: string
  /** 화면 문구 키(ADMIN_COPY.aeEditKinds) — 경로에서 키·번호를 뺀 모양 */
  readonly kind: string
  readonly type: AeEditItemType
  readonly value: unknown
  readonly original: unknown
  readonly deleted: boolean
  /** 행 단위 지우기 경로(행의 칸이면) */
  readonly row: string | null
  readonly rowDeleted: boolean
  /** 행의 키(키워드 문장·제목·라벨) 또는 번호 */
  readonly context: string
  readonly deletable: boolean
  /** 행 단위로 지울 수 있는가(카드는 칸만 지운다) */
  readonly rowDeletable: boolean
  readonly overridden: boolean
  readonly numbersChanged: boolean
}

export type AeEditCase = {
  readonly label: string
  readonly keyword: string
  readonly excerpt: string
  readonly platform: string
  readonly measuredOn: string
  readonly insight: string
}

export type AeEditSummary = {
  readonly keyLines: readonly string[]
  readonly cardValue: string
  readonly cardNames: readonly string[]
  readonly cardNamesTail: string | null
  readonly cases: { readonly found: AeEditCase | null; readonly missing: AeEditCase | null }
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

export type AeEditNumberChange = { readonly path: string; readonly before: unknown; readonly after: unknown }

export type AeEditPreview = {
  readonly reportId: string
  readonly reportVersion: number
  readonly items: readonly AeEditItem[]
  /** 시스템이 만든 원문(수정 전)의 요약 — 사례 선택 화면이 읽는다. */
  readonly original: AeEditSummary
  /** 지금 저장된 버전이 실제로 찍는 요약. */
  readonly current: AeEditSummary
  /** 요청한 수정을 적용한 결과의 요약. 요청이 없거나 거절됐으면 null. */
  readonly preview: AeEditSummary | null
  readonly overrides: Readonly<Record<string, unknown>>
  readonly problems: readonly AeEditProblem[]
  readonly numbersPreserved: boolean
  readonly factChanges: Readonly<Record<string, { readonly before: unknown; readonly after: unknown }>>
  readonly numberChanges: readonly AeEditNumberChange[]
  readonly candidates: { readonly found: readonly AeEditCandidate[]; readonly missing: readonly AeEditCandidate[] }
  readonly editedBy: string | null
  readonly editedAt: string | null
  readonly droppedPaths: readonly string[]
  readonly redeliveryNote: string
}

/** 사례 선택 상태: 'keep' = 지금 사례 그대로, 'revert' = 시스템 원래 사례로, 아니면 후보 기록 id. */
export type AeEditCaseChoice = 'keep' | 'revert' | string

export const AE_DELETE = { deleted: true } as const

export function isDeleteMarker(value: unknown): boolean {
  return isRecord(value) && value.deleted === true && Object.keys(value).length === 1
}

/**
 * 화면 입력란의 상태 — `values[path]`는 입력란 값(글자·숫자 문자열·목록) 또는 `AE_DELETE`(지움), `rows[row]`는 행을
 * 지웠는지. 값은 '지금 찍히는 값'(저장된 수정 포함)에서 시작하고, 서버 `fields`로 바꿀 때 저장된 상태와 비교해
 * **실제로 바뀐 칸만** 보낸다.
 */
export type AeEditDraft = {
  readonly values: Readonly<Record<string, unknown>>
  readonly rows: Readonly<Record<string, boolean>>
  readonly caseRecords: { readonly found: AeEditCaseChoice; readonly missing: AeEditCaseChoice }
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
  }
}

const ITEM_TYPES: readonly AeEditItemType[] = ['text', 'label', 'count', 'pct', 'pct_opt', 'int', 'state', 'list']

function parseItem(raw: unknown): AeEditItem | null {
  if (!isRecord(raw) || typeof raw.path !== 'string') return null
  const type = ITEM_TYPES.find((candidate) => candidate === raw.type) ?? 'text'
  return {
    path: raw.path,
    section: text(raw.section, 'p1'),
    kind: text(raw.kind, raw.path),
    type,
    value: raw.value ?? null,
    original: raw.original ?? null,
    deleted: raw.deleted === true,
    row: typeof raw.row === 'string' ? raw.row : null,
    rowDeleted: raw.row_deleted === true,
    context: text(raw.context),
    deletable: raw.deletable !== false,
    rowDeletable: raw.row_deletable !== false,
    overridden: raw.overridden === true,
    numbersChanged: raw.numbers_changed === true,
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

function parseProblems(value: unknown): AeEditProblem[] {
  return (Array.isArray(value) ? value : []).flatMap((raw) => {
    if (!isRecord(raw)) return []
    return [{ path: text(raw.path), code: text(raw.code), message: text(raw.message, '검증에 걸렸습니다.') }]
  })
}

/** 종전 별칭 `plan.action[K]`를 새 경로 `plan.rows[K].action`으로 — 서버 `doctor_report_v4.canonical_ae_path`와 같다. */
export function canonicalAePath(path: string): string {
  const match = /^plan\.action\[([\s\S]+)\]$/.exec(path)
  return match ? `plan.rows[${match[1]}].action` : path
}

function canonicalOverrides(value: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (!isRecord(value)) return out
  for (const [path, stored] of Object.entries(value)) out[canonicalAePath(path)] = stored
  return out
}

export function parseAeEditPreview(payload: unknown): AeEditPreview | null {
  if (!isRecord(payload)) return null
  const original = parseSummary(payload.editable)
  if (!original || typeof payload.report_id !== 'string') return null
  const current = parseSummary(payload.current) ?? original
  const candidates = isRecord(payload.candidates) ? payload.candidates : {}
  const dropped = isRecord(payload.dropped) && isRecord(payload.dropped.fields) ? Object.keys(payload.dropped.fields) : []
  const factChanges: Record<string, { before: unknown; after: unknown }> = {}
  if (isRecord(payload.fact_changes)) {
    for (const [key, change] of Object.entries(payload.fact_changes)) {
      if (isRecord(change)) factChanges[key] = { before: change.before ?? null, after: change.after ?? null }
    }
  }
  return {
    reportId: payload.report_id,
    reportVersion: typeof payload.report_version === 'number' ? payload.report_version : 0,
    items: (Array.isArray(payload.items) ? payload.items : []).flatMap((raw) => { const item = parseItem(raw); return item ? [item] : [] }),
    original,
    current,
    preview: parseSummary(payload.preview),
    overrides: canonicalOverrides(payload.overrides),
    problems: parseProblems(payload.problems),
    numbersPreserved: payload.numbers_preserved === true,
    factChanges,
    numberChanges: (Array.isArray(payload.number_changes) ? payload.number_changes : []).flatMap((raw) => {
      if (!isRecord(raw) || typeof raw.path !== 'string') return []
      return [{ path: raw.path, before: raw.before ?? null, after: raw.after ?? null }]
    }),
    candidates: { found: parseCandidates(candidates.found), missing: parseCandidates(candidates.missing) },
    editedBy: typeof payload.edited_by === 'string' ? payload.edited_by : null,
    editedAt: typeof payload.edited_at === 'string' ? payload.edited_at : null,
    droppedPaths: dropped,
    redeliveryNote: text(payload.redelivery_note, '새 버전은 다시 전달해야 원장님께 반영됩니다.'),
  }
}

/** 지금 저장된 상태(원문 + 저장된 수정)로 입력란을 채운다 — 지운 항목은 `AE_DELETE`, 사례 선택은 '지금 사례 유지'. */
export function draftFromItems(items: readonly AeEditItem[]): AeEditDraft {
  const values: Record<string, unknown> = {}
  const rows: Record<string, boolean> = {}
  for (const item of items) {
    values[item.path] = item.deleted ? AE_DELETE : item.value
    if (item.row) rows[item.row] = item.rowDeleted
  }
  return { values, rows, caseRecords: { found: 'keep', missing: 'keep' } }
}

export const AE_CARD_NAMES_PATH = 'summary.cards[3].names'

export function caseRecordPath(slot: 'found' | 'missing'): string {
  return `cases.${slot}.record_id`
}

const NUMBER = /\d+(?:\.\d+)?/g
const NUMERIC_TYPES: readonly AeEditItemType[] = ['count', 'pct', 'pct_opt', 'int']

export function parseAeNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string') return null
  const trimmed = value.trim().replace(/%$/, '').trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function tokens(value: unknown): string[] {
  const source = Array.isArray(value) ? value.join(' ') : typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value)
  return source.match(NUMBER) ?? []
}

/**
 * 값이 측정값(원문)과 다른 숫자를 말하는가 — 서버 `doctor_report_ae_edit.numbers_differ`와 같은 규칙. 수는 값으로,
 * 글자는 원문에 없던 숫자 토큰이 있는지로 본다(숫자를 뺀 문장은 '다름'이 아니다). 화면이 '측정값과 다름' 표시에 쓴다.
 */
export function numbersDiffer(item: Pick<AeEditItem, 'type' | 'original'>, value: unknown): boolean {
  if (isDeleteMarker(value)) return false
  if (NUMERIC_TYPES.includes(item.type)) {
    const before = parseAeNumber(item.original)
    const after = parseAeNumber(value)
    if (before === null) return after !== null
    return after === null || Math.abs(after - before) > 1e-9
  }
  const before = new Map<string, number>()
  for (const token of tokens(item.original)) before.set(token, (before.get(token) ?? 0) + 1)
  for (const token of tokens(value)) {
    const left = before.get(token) ?? 0
    if (left === 0) return true
    before.set(token, left - 1)
  }
  return false
}

function normalizeValue(item: Pick<AeEditItem, 'type'>, value: unknown): unknown {
  if (isDeleteMarker(value)) return AE_DELETE
  if (item.type === 'list') {
    const list = Array.isArray(value) ? value : typeof value === 'string' ? value.split(',') : []
    return list.map((entry) => String(entry).trim()).filter((entry) => entry.length > 0)
  }
  if (NUMERIC_TYPES.includes(item.type)) {
    if (value === null || value === undefined || (typeof value === 'string' && value.trim() === '')) return null
    const parsed = parseAeNumber(value)
    return parsed === null ? String(value).trim() : parsed
  }
  return typeof value === 'string' ? value.trim() : value === null || value === undefined ? '' : String(value)
}

function sameValue(a: unknown, b: unknown): boolean {
  if (isDeleteMarker(a) || isDeleteMarker(b)) return isDeleteMarker(a) && isDeleteMarker(b)
  if (Array.isArray(a) || Array.isArray(b)) {
    const left = Array.isArray(a) ? a : []
    const right = Array.isArray(b) ? b : []
    return left.length === right.length && left.every((item, index) => item === right[index])
  }
  if (typeof a === 'number' || typeof b === 'number') {
    const left = parseAeNumber(a)
    const right = parseAeNumber(b)
    return left !== null && right !== null ? Math.abs(left - right) < 1e-9 : a === b
  }
  return a === b
}

/**
 * 입력란 상태 → 서버 `fields`. 저장된 상태(원문 또는 저장된 수정·지움)와 같은 칸은 보내지 않는다 — 저장된 수정은
 * 서버가 합쳐 그대로 잇는다. 바꾼 칸은 값을, 지운 칸은 `{deleted: true}`를, 저장된 수정이 있는 칸을 원문으로 되돌렸으면
 * null(수정 취소)을 보낸다. 행 지우기·되살리기는 행 경로로 보낸다.
 */
export function buildAeEditFields(
  items: readonly AeEditItem[],
  draft: AeEditDraft,
  overrides: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  const fields: Record<string, unknown> = {}
  const seenRows = new Set<string>()
  for (const item of items) {
    if (item.row && item.rowDeletable && !seenRows.has(item.row)) {
      seenRows.add(item.row)
      const wasDeleted = isDeleteMarker(overrides[item.row])
      const nowDeleted = draft.rows[item.row] === true
      if (nowDeleted && !wasDeleted) fields[item.row] = AE_DELETE
      else if (!nowDeleted && wasDeleted) fields[item.row] = null
    }
    if (!(item.path in draft.values)) continue
    const next = normalizeValue(item, draft.values[item.path])
    const stored = item.path in overrides ? (isDeleteMarker(overrides[item.path]) ? AE_DELETE : normalizeValue(item, overrides[item.path])) : undefined
    const original = normalizeValue(item, item.original)
    const storedState = stored === undefined ? original : stored
    if (sameValue(next, storedState)) continue
    if (sameValue(next, original)) {
      if (item.path in overrides) fields[item.path] = null
      continue
    }
    fields[item.path] = next
  }
  for (const slot of ['found', 'missing'] as const) {
    const choice = draft.caseRecords[slot]
    if (choice === 'revert') {
      if (caseRecordPath(slot) in overrides) fields[caseRecordPath(slot)] = null
    } else if (choice !== 'keep') fields[caseRecordPath(slot)] = choice
  }
  return fields
}

export type AeEditGroup = {
  /** 행 경로(행 묶음) 또는 첫 항목의 경로 */
  readonly key: string
  readonly row: string | null
  readonly rowDeletable: boolean
  readonly context: string
  readonly items: readonly AeEditItem[]
}

export type AeEditSectionGroup = { readonly section: string; readonly groups: readonly AeEditGroup[] }

/** 항목을 절 → 행 묶음으로 나눈다(보고서 순서 그대로). 행이 없는 항목은 저마다 한 묶음이다. */
export function groupAeEditItems(items: readonly AeEditItem[]): AeEditSectionGroup[] {
  const sections: AeEditSectionGroup[] = []
  let current: { section: string; groups: AeEditGroup[] } | null = null
  let lastGroup: { key: string; row: string | null; rowDeletable: boolean; context: string; items: AeEditItem[] } | null = null
  for (const item of items) {
    if (!current || current.section !== item.section) {
      current = { section: item.section, groups: [] }
      sections.push(current)
      lastGroup = null
    }
    if (item.row && lastGroup && lastGroup.row === item.row) {
      lastGroup.items.push(item)
      continue
    }
    lastGroup = { key: item.row ?? item.path, row: item.row, rowDeletable: item.row !== null && item.rowDeletable, context: item.context, items: [item] }
    current.groups.push(lastGroup)
  }
  return sections
}

/** 라벨 수정으로 카드 4가 다시 세어졌으면(검증 미리보기 ≠ 지금 카드) 그 카드를 돌려준다. */
export function recountedCard(
  current: AeEditSummary,
  preview: AeEditSummary | null,
): { readonly value: string; readonly names: readonly string[]; readonly tail: string | null } | null {
  if (!preview) return null
  const same = preview.cardValue === current.cardValue && preview.cardNamesTail === current.cardNamesTail && sameValue(preview.cardNames, current.cardNames)
  return same ? null : { value: preview.cardValue, names: preview.cardNames, tail: preview.cardNamesTail }
}

/** 작업 기록에서 이 요청의 결과를 읽는다 — 실패한 작업도 그 달의 기존 보고서 버전을 싣기 때문에 버전이 **올랐는지** 본다. */
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

/** 거절 사유를 칸 옆에 보여 주기 위한 경로 → 메시지 묶음(경로 없는 사유는 ''). 목록 항목(`…[2]`)은 그 칸 전체에 붙인다. */
export function problemsByPath(problems: readonly AeEditProblem[], knownPaths: ReadonlySet<string> = new Set()): Readonly<Record<string, string[]>> {
  const grouped: Record<string, string[]> = {}
  for (const problem of problems) {
    let path = problem.path
    const match = /^(.+)\[\d+\]$/.exec(path)
    if (match && (path.startsWith(`${AE_CARD_NAMES_PATH}[`) || (knownPaths.has(match[1]) && !knownPaths.has(path)))) path = match[1]
    ;(grouped[path] ??= []).push(problem.message)
  }
  return grouped
}

/** 서버 422(`AE_EDIT_REJECTED`·`AE_EDIT_PDF_INVALID`) 본문에서 문제 목록을 읽는다. 다른 모양이면 빈 목록. */
export function problemsFromRejection(detail: unknown): AeEditProblem[] {
  if (!isRecord(detail) || !Array.isArray(detail.problems)) return []
  return parseProblems(detail.problems)
}

/** 입력란에 보일 글자 — 목록은 쉼표로, 비움은 빈 글자, 수는 그대로. */
export function displayValue(item: Pick<AeEditItem, 'type'>, value: unknown): string {
  if (isDeleteMarker(value) || value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join(', ')
  return String(value)
}
