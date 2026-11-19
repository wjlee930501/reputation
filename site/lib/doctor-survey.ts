/**
 * 원장 사전 설문 — 화면 흐름·답 판정(순수 함수).
 *
 * 문항 정의는 서버(`backend/app/services/doctor_survey_schema.py`)가 내려 준다. 여기 있는 판정은 서버의
 * `active_fields`·`question_answered`·`visible_questions`와 같은 규칙이어야 한다 — 진행률과 '비어 있는
 * 문항' 목록이 Admin 진행률과 어긋나지 않게.
 */

export type SurveyOption = string | [string, string]

export interface SurveyField {
  t: string
  key?: string
  label?: string
  ph?: string
  opts?: SurveyOption[]
  custom?: boolean
  custom_ph?: string
  reveal?: Record<string, SurveyField[]>
  children?: SurveyField[]
  children_off?: SurveyField[]
  items?: SurveyField[]
  fields?: SurveyField[]
  max?: number
  unit?: string
  col?: boolean
  none?: string
  src?: 'treat' | 'aiq'
  row_label?: string
  add?: string
  rows?: number
  text?: string
  title?: string
  d?: string
  accept?: string
  kind?: string
  sub?: string
  role?: string
}

export interface SurveyQuestion {
  id: string
  n: string
  t: string
  d?: string
  why?: string
  new_clinic_only?: boolean
  b: SurveyField[]
}

export interface SurveyPart {
  name: string
  sum: string
  focus?: boolean
  qs: SurveyQuestion[]
}

export interface SurveySchema {
  version: string
  intro: {
    title: string
    desc: string
    badges: string[]
    notice: string
    respondent_label: string
    respondents: [string, string][]
    consent: { version: string; title: string; items: string[]; label: string }
  }
  review: { title: string; desc: string }
  done: { title: string; desc: string; edit_note: string }
  parts: SurveyPart[]
  focus_questions: SurveyQuestion[]
  focus_key: string
  focus_max: number
  ai_question_templates: string[]
  ai_question_fallbacks: Record<'town' | 'dept' | 'focus', string>
  spec_groups: [string, string[]][]
  times: string[]
  bio_kinds: string[]
  doctor_titles: string[]
  uploads_enabled: boolean
  files_pending_notice: string
}

export type Answers = Record<string, unknown>

export interface FlowItem {
  key: string
  question?: SurveyQuestion
  partIndex?: number
  focus?: string
}

export function optionValue(option: SurveyOption): string {
  return Array.isArray(option) ? option[0] : option
}

export function hasValue(value: unknown): boolean {
  if (value === null || value === undefined || value === false) return false
  if (typeof value === 'string') return value.trim().length > 0
  if (Array.isArray(value)) return value.some(hasValue)
  // 줄 id(`_rid`)는 답이 아니다.
  if (typeof value === 'object')
    return Object.entries(value as Record<string, unknown>).some(([key, item]) => key !== '_rid' && hasValue(item))
  return true
}

/** 같은 층의 입력 칸 전부(펼침·스위치 아래 포함). 서버 `iter_fields`와 같다. */
export function iterFields(fields: SurveyField[]): SurveyField[] {
  const out: SurveyField[] = []
  for (const field of fields) {
    if (field.t === 'toggles') {
      out.push(...iterFields(field.items ?? []))
      continue
    }
    if (field.key) out.push(field)
    for (const children of Object.values(field.reveal ?? {})) out.push(...iterFields(children))
    out.push(...iterFields(field.children ?? []))
    out.push(...iterFields(field.children_off ?? []))
  }
  return out
}

export function focusTreatments(answers: Answers, max: number): string[] {
  const raw = answers.focus_treatments
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is string => typeof item === 'string' && item.trim() !== '').slice(0, max)
}

export function focusScope(answers: Answers, schema: SurveySchema, focus: string): Answers {
  const all = answers[schema.focus_key]
  if (!all || typeof all !== 'object') return {}
  const block = (all as Record<string, unknown>)[focus]
  return block && typeof block === 'object' ? (block as Answers) : {}
}

/** 시작 → 문항(3부는 주력 진료마다) → 확인 → 완료. 서버 `visible_questions`와 같은 순서다. */
export function buildFlow(schema: SurveySchema, answers: Answers, newClinic: boolean): FlowItem[] {
  const flow: FlowItem[] = [{ key: 'intro' }]
  const focus = focusTreatments(answers, schema.focus_max)
  schema.parts.forEach((part, partIndex) => {
    if (part.focus) {
      for (const name of focus) {
        for (const question of schema.focus_questions) {
          flow.push({ key: `focus|${name}|${question.id}`, question, partIndex, focus: name })
        }
      }
      return
    }
    for (const question of part.qs) {
      if (question.new_clinic_only && !newClinic) continue
      flow.push({ key: `q|${question.id}`, question, partIndex })
    }
  })
  flow.push({ key: 'review' }, { key: 'done' })
  return flow
}

export function questionAnswered(schema: SurveySchema, answers: Answers, item: FlowItem): boolean {
  if (!item.question) return false
  const scope = item.focus !== undefined ? focusScope(answers, schema, item.focus) : answers
  return iterFields(item.question.b).some((field) => field.key !== undefined && hasValue(scope[field.key]))
}

export function progress(schema: SurveySchema, answers: Answers, newClinic: boolean) {
  const questions = buildFlow(schema, answers, newClinic).filter((item) => item.question)
  const answered = questions.filter((item) => questionAnswered(schema, answers, item)).length
  return { answered, total: questions.length }
}

/** '우리 병원이 나왔으면 하는 AI 질문'의 보기 — 동네·진료과·주력 진료로 채운다. */
export function aiQuestionOptions(schema: SurveySchema, answers: Answers): string[] {
  const first = (key: string): string | undefined => {
    const value = answers[key]
    return Array.isArray(value) && typeof value[0] === 'string' && value[0].trim() ? value[0].trim() : undefined
  }
  const fill: Record<string, string> = {
    town: first('towns') ?? schema.ai_question_fallbacks.town,
    dept: first('departments') ?? schema.ai_question_fallbacks.dept,
    focus: focusTreatments(answers, schema.focus_max)[0] ?? schema.ai_question_fallbacks.focus,
  }
  return schema.ai_question_templates.map((template) =>
    template.replace(/\{(town|dept|focus)\}/g, (_match, name: string) => fill[name] ?? ''),
  )
}

/** 진료비 칸의 진료 보기 — 5번 '진료 항목'에 적은 이름. */
export function treatmentNames(answers: Answers): string[] {
  const rows = answers.treatments
  if (!Array.isArray(rows)) return []
  const names: string[] = []
  for (const row of rows) {
    const name = row && typeof row === 'object' ? (row as Record<string, unknown>).name : undefined
    if (typeof name === 'string' && name.trim() && !names.includes(name.trim())) names.push(name.trim())
  }
  return names
}

export function questionTitle(question: SurveyQuestion, focus?: string): string {
  return focus === undefined ? question.t : question.t.split('{nm}').join(focus)
}

export function questionDesc(question: SurveyQuestion, focus?: string): string | undefined {
  if (!question.d) return undefined
  return focus === undefined ? question.d : question.d.split('{nm}').join(focus)
}

/** 화면 key(`q|…`, `focus|이름|…`, `review`)가 지금 흐름에 없으면 처음 문항으로. */
export function resolveStep(flow: FlowItem[], key: string | null | undefined): number {
  const index = key ? flow.findIndex((item) => item.key === key) : -1
  return index >= 0 ? index : 0
}

/** 저장할 답에서 빈 값을 걷는다(서버도 같은 일을 하지만 요청을 작게). */
export function compactAnswers(answers: Answers): Answers {
  const out: Answers = {}
  for (const [key, value] of Object.entries(answers)) {
    if (hasValue(value) || value === false) out[key] = value
  }
  return out
}
