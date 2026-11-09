/**
 * 콘텐츠 방향(주력 주제) — 서버 응답 모양과 화면 계산.
 * 저장·판정은 서버(`backend/app/services/content_focus.py`)가 한다. 여기서는 보여 줄 문장만 만든다.
 */

export interface ContentFocusTopicMatch {
  topic: string
  matching_questions: number
  examples: string[]
}

export interface ContentFocusMonth {
  month: string // YYYY-MM
  active: boolean
  total: number
  focused: number
  quota: number
  open_slots: number
}

export interface ContentFocusState {
  topics: string[]
  share: number
  from_month: string | null
  until_month: string | null
  legacy_topics: string[]
  share_choices: number[]
  max_topics: number
  topic_matches: ContentFocusTopicMatch[]
  months: ContentFocusMonth[]
}

export interface ContentFocusDraft {
  topics: string[]
  share: number
  fromMonth: string
  untilMonth: string
}

export const SHARE_LABELS: Record<number, string> = {
  30: '조금',
  50: '절반',
  70: '대부분',
  100: '전부',
}

export const TOPIC_MIN_CHARS = 2
export const TOPIC_MAX_CHARS = 20

export function draftFromState(state: ContentFocusState | null): ContentFocusDraft {
  return {
    topics: [...(state?.topics ?? [])],
    share: state?.share ?? 50,
    fromMonth: state?.from_month ?? '',
    untilMonth: state?.until_month ?? '',
  }
}

export function normalizeTopic(value: string): string {
  return value.replace(/\s+/g, ' ').trim()
}

/** 주제 하나를 더할 때의 문제. 없으면 null. */
export function topicInputProblem(value: string, existing: string[], maxTopics: number): string | null {
  const topic = normalizeTopic(value)
  if (!topic) return null
  if (existing.includes(topic)) return '이미 넣은 주제입니다.'
  if (topic.length < TOPIC_MIN_CHARS || topic.length > TOPIC_MAX_CHARS) {
    return `주제는 ${TOPIC_MIN_CHARS}~${TOPIC_MAX_CHARS}자로 적어 주세요.`
  }
  if (existing.length >= maxTopics) return `주력 주제는 ${maxTopics}개까지 정할 수 있습니다.`
  return null
}

export function monthLabel(month: string): string {
  const [year, mm] = month.split('-')
  const number = Number(mm)
  if (!year || !number) return month
  return `${year}년 ${number}월`
}

export function shortMonthLabel(month: string): string {
  const number = Number(month.split('-')[1])
  return number ? `${number}월` : month
}

export function periodLabel(fromMonth: string | null | undefined, untilMonth: string | null | undefined): string {
  if (fromMonth && untilMonth) {
    return fromMonth === untilMonth ? monthLabel(fromMonth) : `${monthLabel(fromMonth)} ~ ${monthLabel(untilMonth)}`
  }
  if (fromMonth) return `${monthLabel(fromMonth)}부터 계속`
  if (untilMonth) return `지금부터 ${monthLabel(untilMonth)}까지`
  return '기간 없이 계속'
}

/** 카드 머리의 한 줄 요약. */
export function focusSummary(state: ContentFocusState | null): string {
  if (!state || state.topics.length === 0) return '꺼짐'
  const [first, ...rest] = state.topics
  const topics = rest.length > 0 ? `${first} 외 ${rest.length}개` : first
  return `켜짐 · ${topics} · ${periodLabel(state.from_month, state.until_month)} · ${state.share}%`
}

/** 그 달 진행 문장. 꺼진 달이면 null. */
export function monthProgress(month: ContentFocusMonth): string | null {
  if (!month.active) return null
  return `${shortMonthLabel(month.month)} 글 ${month.total}편 중 주력 주제 목표 ${month.quota}편 · 지금 ${month.focused}편 · 아직 주제가 정해지지 않은 글 ${month.open_slots}편`
}

/** 남은 빈 글로는 목표를 다 못 채우는 달의 안내. 채울 수 있으면 null. */
export function monthShortfall(month: ContentFocusMonth): string | null {
  if (!month.active) return null
  const needed = Math.max(0, month.quota - month.focused)
  if (needed === 0 || month.open_slots >= needed) return null
  return `${shortMonthLabel(month.month)}은 주제가 이미 정해진 글이 많아 목표보다 ${needed - month.open_slots}편 적게 쓰일 수 있습니다.`
}

export function matchLabel(match: ContentFocusTopicMatch | undefined): { text: string; tone: 'good' | 'warn' | 'pending' } {
  if (!match) return { text: '맞는 환자 질문 확인 중', tone: 'pending' }
  if (match.matching_questions === 0) {
    return { text: '맞는 환자 질문 없음 — 이 주제로는 글을 고를 수 없습니다', tone: 'warn' }
  }
  return { text: `맞는 환자 질문 ${match.matching_questions}개`, tone: 'good' }
}

export function draftChanged(state: ContentFocusState | null, draft: ContentFocusDraft): boolean {
  const saved = draftFromState(state)
  return (
    saved.share !== draft.share
    || saved.fromMonth !== draft.fromMonth
    || saved.untilMonth !== draft.untilMonth
    || saved.topics.length !== draft.topics.length
    || saved.topics.some((topic, index) => topic !== draft.topics[index])
  )
}

/** 이번 달 기준 `YYYY-MM`. 화면의 "이번 달부터 2개월" 버튼이 쓴다. */
export function addMonths(month: string, offset: number): string {
  const [year, mm] = month.split('-').map(Number)
  const index = year * 12 + (mm - 1) + offset
  const nextYear = Math.floor(index / 12)
  const nextMonth = (index % 12) + 1
  return `${nextYear}-${String(nextMonth).padStart(2, '0')}`
}

export function periodProblem(fromMonth: string, untilMonth: string): string | null {
  if (fromMonth && untilMonth && fromMonth > untilMonth) return '끝 달이 시작 달보다 앞설 수 없습니다.'
  return null
}
