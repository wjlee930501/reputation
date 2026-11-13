/**
 * 병원 사이트 레이아웃 A/B/C — 마케터가 고르는 표현 계층.
 *
 * 레이아웃은 화면 구성만 바꾼다. 구조화 데이터·llms.txt·FAQ·질문 목록의 선택은 페이지가
 * 한 번만 하고, 레이아웃은 받은 값을 그리기만 한다(docs/plans/2026-11-13-clinic-site-layouts-abc.md).
 * 이 파일은 레이아웃이 함께 쓰는 순수 함수만 둔다 — 테스트가 컴포넌트 없이 규칙을 확인한다.
 */
import type { ContentSummary } from './api.ts'
import { isClosedLabel } from './business-hours.ts'

export type ClinicLayout = 'A' | 'B' | 'C'

/** 공개 응답의 값 → 레이아웃. 모르는 값과 null은 레이아웃 도입 전 화면(null)이다. */
export function resolveClinicLayout(value: unknown): ClinicLayout | null {
  return value === 'A' || value === 'B' || value === 'C' ? value : null
}

/**
 * 질문 목록이 고르는 순서 — 환자가 실제로 던지는 질문 형태가 먼저다.
 * AnswerClusters(레이아웃 도입 전 화면)와 레이아웃 A·C가 같은 규칙을 쓴다.
 */
export const QUESTION_PRIORITY = ['FAQ', 'DISEASE', 'TREATMENT', 'HEALTH', 'LOCAL', 'COLUMN', 'NOTICE']

export function selectAnswerQuestions<T extends Pick<ContentSummary, 'content_type'>>(
  contents: readonly T[],
  limit: number,
): T[] {
  const rank = (type: string) => {
    const index = QUESTION_PRIORITY.indexOf(type)
    return index === -1 ? 99 : index
  }
  return [...contents].sort((a, b) => rank(a.content_type) - rank(b.content_type)).slice(0, limit)
}

/** 질문 목록 한 줄의 질문 문장 — 승인된 질문이 있으면 그것, 없으면 글 제목. */
export function questionText(content: Pick<ContentSummary, 'faq_question' | 'title'>): string {
  return content.faq_question || content.title
}

/** 질문 목록 한 줄의 답변 요약 — 승인된 답변 요약, 없으면 글 요약. */
export function questionSummary(
  content: Pick<ContentSummary, 'faq_answer_summary' | 'meta_description'>,
): string | null {
  return content.faq_answer_summary || content.meta_description || null
}

const SEOUL_DAY_KEYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const
const DAY_SHORT_LABELS: Record<string, string> = {
  mon: '월',
  tue: '화',
  wed: '수',
  thu: '목',
  fri: '금',
  sat: '토',
  sun: '일',
}
export const WEEK_DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

/** 한국 시간 기준 오늘 — 서버(UTC)에서 KST 00:00~09:00 사이에 전날로 보이지 않게 한다. */
export function seoulToday(now: Date = new Date()): { key: string; label: string; month: number; day: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    weekday: 'short',
    month: 'numeric',
    day: 'numeric',
  }).formatToParts(now)
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? ''
  const key = get('weekday').toLowerCase()
  const normalized = (SEOUL_DAY_KEYS as readonly string[]).includes(key) ? key : 'mon'
  return {
    key: normalized,
    label: DAY_SHORT_LABELS[normalized] ?? '',
    month: Number(get('month')),
    day: Number(get('day')),
  }
}

export function dayShortLabel(key: string): string {
  return DAY_SHORT_LABELS[key] ?? key
}

/** 오늘의 진료시간 문자열. 없으면 null, 휴진이면 closed. */
export function todayHours(
  hours: Record<string, string> | null | undefined,
  todayKey: string,
): { time: string; closed: boolean } | null {
  const time = hours?.[todayKey]?.trim()
  if (!time) return null
  return { time, closed: isClosedLabel(time) }
}

/**
 * '08:00 ~ 18:30 (점심 12:30 ~ 14:00)'을 본 시간과 괄호 안 메모로 나눈다. 좁은 칸에서 괄호가
 * 줄 중간에서 끊기지 않게 메모를 둘째 줄로 내릴 때 쓴다. 글자는 바꾸지 않는다.
 */
export function splitHoursNote(time: string): { main: string; note: string | null } {
  const match = /^(.*?\S)\s*(\([^()]*\))$/.exec(time.trim())
  return match ? { main: match[1], note: match[2] } : { main: time.trim(), note: null }
}

export interface SessionCell {
  /** open: 그 칸 전체 진료 · partial: 진료는 하지만 시간이 칸과 다름 · closed: 진료 없음 */
  state: 'open' | 'partial' | 'closed'
}

export interface SessionRow {
  label: string
  cells: SessionCell[]
}

export interface SessionGrid {
  rows: SessionRow[]
  hasPartial: boolean
}

interface DayHours {
  closed: boolean
  opens: number
  closes: number
  breakStart: number | null
  breakEnd: number | null
}

const TIME_RE = /(\d{1,2}):(\d{2})/g

/** '08:30' → 510. 시각이 아닌 값(25:00·08:75)은 NaN — 부르는 쪽이 표를 포기한다. */
function minutes(text: string): number {
  const [h, m] = text.split(':').map(Number)
  if (!(h >= 0 && h <= 24 && m >= 0 && m < 60) || (h === 24 && m > 0)) return Number.NaN
  return h * 60 + m
}

function clock(total: number): string {
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function parseDay(value: string | undefined): DayHours | 'unknown' {
  const text = value?.trim()
  // 입력이 없는 요일은 휴진이라고 단정하지 않는다 — 표를 포기하고 요일별 목록이 말하게 한다.
  if (!text) return 'unknown'
  if (isClosedLabel(text) && !/\d{1,2}:\d{2}/.test(text.replace(/\([^)]*\)/g, ''))) {
    return { closed: true, opens: 0, closes: 0, breakStart: null, breakEnd: null }
  }
  // 괄호 밖이 진료시간, 괄호 안이 점심·휴게시간이다("08:00 ~ 18:00 (점심 13:00 ~ 14:00)").
  const main = text.replace(/\([^)]*\)/g, ' ')
  const mainTimes = [...main.matchAll(TIME_RE)].map((match) => minutes(match[0]))
  if (mainTimes.length !== 2 || mainTimes.some(Number.isNaN)) return 'unknown'
  const [opens, closes] = mainTimes
  if (!(opens < closes)) return 'unknown'
  const notes = [...text.matchAll(/\(([^)]*)\)/g)].map((match) => match[1]).join(' ')
  const breakTimes = /점심|휴게|브레이크|break/i.test(notes)
    ? [...notes.matchAll(TIME_RE)].map((match) => minutes(match[0]))
    : []
  const hasBreak = breakTimes.length === 2
  // 점심시간을 적었는데 진료시간 안에 순서대로 들어가지 않으면 잘못 적은 값이다 — 표를 포기한다.
  if (hasBreak && !(opens < breakTimes[0] && breakTimes[0] < breakTimes[1] && breakTimes[1] < closes)) {
    return 'unknown'
  }
  return {
    closed: false,
    opens,
    closes,
    breakStart: hasBreak ? breakTimes[0] : null,
    breakEnd: hasBreak ? breakTimes[1] : null,
  }
}

/**
 * C안의 요일×시간 ●진료표. 오전·오후 칸은 가장 긴 진료일의 점심시간으로 나눈다.
 *
 * 읽을 수 없거나 비어 있는 요일이 하나라도 있으면 null — 표를 지어내지 않고 요일별 목록으로
 * 떨어진다(빈 요일을 휴진으로, 잘못 적은 시간을 진료로 단정하지 않는다).
 * 칸과 시간이 정확히 같지 않은 날(예: 오후 진료가 일찍 끝남)은 'partial'로 표시하고,
 * 정확한 시간은 같은 페이지의 요일별 표가 말한다.
 */
export function buildSessionGrid(hours: Record<string, string> | null | undefined): SessionGrid | null {
  if (!hours) return null
  const days = WEEK_DAY_KEYS.map((key) => parseDay(hours[key]))
  if (days.some((day) => day === 'unknown')) return null
  const open = days.filter((day): day is DayHours => day !== 'unknown' && !day.closed)
  if (open.length === 0) return null

  const reference = [...open].sort((a, b) => b.closes - b.opens - (a.closes - a.opens))[0]
  const start = Math.min(...open.map((day) => day.opens))
  const end = Math.max(...open.map((day) => day.closes))

  const sessions: Array<{ label: string; from: number; to: number }> =
    reference.breakStart !== null && reference.breakEnd !== null
      ? [
          { label: `오전 ${clock(start)}~${clock(reference.breakStart)}`, from: start, to: reference.breakStart },
          { label: `오후 ${clock(reference.breakEnd)}~${clock(end)}`, from: reference.breakEnd, to: end },
        ]
      : [{ label: `진료 ${clock(start)}~${clock(end)}`, from: start, to: end }]

  let hasPartial = false
  const rows = sessions.map((session) => ({
    label: session.label,
    cells: days.map((day): SessionCell => {
      if (day === 'unknown' || day.closed) return { state: 'closed' }
      const overlaps = day.opens < session.to && day.closes > session.from
      if (!overlaps) return { state: 'closed' }
      const covers = day.opens <= session.from && day.closes >= session.to
      const breakInside =
        day.breakStart !== null && day.breakEnd !== null && day.breakStart < session.to && day.breakEnd > session.from
      if (covers && !breakInside) return { state: 'open' }
      hasPartial = true
      return { state: 'partial' }
    }),
  }))
  return { rows, hasPartial }
}
