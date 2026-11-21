// 측정 키워드 30개 세트 편집 — 그룹 A~D, 고정 20 + 교체 10 (docs/plans/2026-11-21-measurement-30-design.md §6).
// 서버(`measurement_set_apply.validate_rows`)가 최종 판정을 하고, 여기서는 저장 전 미리 보이는 개수 검사만 한다.

export type MeasurementGroup = 'A' | 'B' | 'C' | 'D'
export type MeasurementRole = 'FIXED' | 'ROTATING'

export interface MeasurementSetRow {
  target_id?: string | null
  text: string
  group: MeasurementGroup
  role: MeasurementRole | null
  status?: string | null
  problems?: string[]
}

export interface MeasurementSetShape {
  fixed: number
  rotating: number
  candidates: number
  problems: string[]
}

export interface MeasurementSetResponse {
  hospital_id: string
  curated: boolean
  rows: MeasurementSetRow[]
  legacy_questions: { target_id: string; text: string }[]
  shape: MeasurementSetShape
  plan: Record<MeasurementGroup, { fixed: number; rotating: number }>
  next_measurement_at: string
  next_measurement_period: string
  current_period_frozen: boolean
}

export interface MeasurementSetDraftResponse {
  rows: MeasurementSetRow[]
  shape: MeasurementSetShape
}

export const MEASUREMENT_GROUPS: MeasurementGroup[] = ['A', 'B', 'C', 'D']

// 서버 `measurement_set`의 범위와 같다.
export const SET_FIXED_MIN = 15
export const SET_FIXED_MAX = 20
export const SET_ROTATING_MAX = 10
export const SET_TOTAL_MAX = 30
export const QUESTION_MAX_LENGTH = 200
// 서버 저장 본문의 최대 행 수(세트 30 + 후보).
export const ROWS_MAX = 60

export function countRoles(rows: MeasurementSetRow[]): { fixed: number; rotating: number; candidates: number } {
  return {
    fixed: rows.filter((row) => row.role === 'FIXED').length,
    rotating: rows.filter((row) => row.role === 'ROTATING').length,
    candidates: rows.filter((row) => row.role === null).length,
  }
}

export function countGroup(
  rows: MeasurementSetRow[],
  group: MeasurementGroup,
): { fixed: number; rotating: number; candidates: number } {
  return countRoles(rows.filter((row) => row.group === group))
}

/** 저장 전에 보이는 세트 단위 문제(문장). 비어 있으면 저장을 시도할 수 있다. */
export function shapeIssues(rows: MeasurementSetRow[]): string[] {
  const { fixed, rotating } = countRoles(rows)
  const issues: string[] = []
  if (fixed < SET_FIXED_MIN || fixed > SET_FIXED_MAX) {
    issues.push(`고정 질문은 ${SET_FIXED_MIN}~${SET_FIXED_MAX}개여야 합니다(지금 ${fixed}개).`)
  }
  if (rotating > SET_ROTATING_MAX) {
    issues.push(`교체 질문은 ${SET_ROTATING_MAX}개까지입니다(지금 ${rotating}개).`)
  }
  if (fixed + rotating > SET_TOTAL_MAX) {
    issues.push(`측정 질문은 ${SET_TOTAL_MAX}개까지입니다(지금 ${fixed + rotating}개).`)
  }
  if (!rows.some((row) => row.role === 'FIXED' && row.group !== 'A')) {
    issues.push('대표 노출률을 낼 고정 질문(B~D)이 하나 이상 있어야 합니다.')
  }
  const texts = rows.map((row) => row.text.trim())
  if (texts.some((text) => !text)) issues.push('빈 질문이 있습니다.')
  if (texts.some((text) => text.length > QUESTION_MAX_LENGTH)) {
    issues.push(`질문은 ${QUESTION_MAX_LENGTH}자까지입니다.`)
  }
  if (new Set(texts).size !== texts.length) issues.push('같은 질문이 두 번 있습니다.')
  if (rows.length > ROWS_MAX) issues.push(`질문과 후보를 합쳐 ${ROWS_MAX}개까지 둘 수 있습니다(지금 ${rows.length}개).`)
  if (rows.some((row) => row.group === 'A' && row.role === 'ROTATING')) {
    issues.push('병원명 질문(A)은 고정으로만 둡니다.')
  }
  return issues
}

/** 교체 질문과 후보를 맞바꾼다 — 교체 수가 그대로라 세트 모양이 바뀌지 않는다. */
export function swapRotating(
  rows: MeasurementSetRow[],
  rotatingIndex: number,
  candidateIndex: number,
): MeasurementSetRow[] {
  const rotating = rows[rotatingIndex]
  const candidate = rows[candidateIndex]
  if (!rotating || !candidate || rotating.role !== 'ROTATING' || candidate.role !== null) return rows
  return rows.map((row, index) => {
    if (index === rotatingIndex) return { ...row, role: null }
    if (index === candidateIndex) return { ...row, role: 'ROTATING' }
    return row
  })
}

export function setRole(rows: MeasurementSetRow[], index: number, role: MeasurementRole | null): MeasurementSetRow[] {
  return rows.map((row, i) => (i === index ? { ...row, role } : row))
}

export function setText(rows: MeasurementSetRow[], index: number, text: string): MeasurementSetRow[] {
  // 문구를 고치면 서버가 준 문제 표시는 더 맞지 않는다 — 저장할 때 다시 받는다.
  return rows.map((row, i) => (i === index ? { ...row, text, problems: [] } : row))
}

export function addRow(rows: MeasurementSetRow[], group: MeasurementGroup): MeasurementSetRow[] {
  return [...rows, { text: '', group, role: null, problems: [] }]
}

export function removeRow(rows: MeasurementSetRow[], index: number): MeasurementSetRow[] {
  return rows.filter((_, i) => i !== index)
}

export function toSavePayload(rows: MeasurementSetRow[]): { rows: { text: string; group: MeasurementGroup; role: MeasurementRole | null }[] } {
  return {
    rows: rows.map((row) => ({ text: row.text.trim(), group: row.group, role: row.role })),
  }
}

/** 서버 422(`MEASUREMENT_SET_INVALID`)의 문제 목록을 행별 문장과 세트 문장으로 나눈다. */
export function readSaveProblems(detail: unknown): { byIndex: Map<number, string[]>; shape: string[] } {
  const byIndex = new Map<number, string[]>()
  const shape: string[] = []
  // FastAPI 본문 검증 오류(배열 [{loc, msg}])도 세트 문장으로 보인다.
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string') {
        shape.push((item as { msg: string }).msg)
      }
    }
    return { byIndex, shape }
  }
  const problems =
    detail && typeof detail === 'object' && 'problems' in detail && Array.isArray((detail as { problems: unknown }).problems)
      ? ((detail as { problems: unknown[] }).problems)
      : []
  for (const problem of problems) {
    if (!problem || typeof problem !== 'object') continue
    const { index, messages } = problem as { index?: unknown; messages?: unknown }
    const lines = Array.isArray(messages) ? messages.filter((m): m is string => typeof m === 'string') : []
    if (typeof index === 'number') byIndex.set(index, lines)
    else shape.push(...lines)
  }
  return { byIndex, shape }
}
