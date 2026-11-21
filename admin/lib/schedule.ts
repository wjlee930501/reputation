export const DAYS = ['월', '화', '수', '목', '금', '토', '일'] as const

export const PLAN_TOTALS: Record<string, number> = {
  PLAN_20: 20,
  PLAN_16: 16,
  PLAN_12: 12,
}

/** 발행 요일 하루에 놓을 수 있는 최대 편수의 선택지(backend `MAX_DAILY_ARTICLES`와 같다). */
export const DAILY_MAX_OPTIONS = [1, 2, 3] as const

export const DEFAULT_PUBLISH_DAYS_BY_PLAN: Record<string, number[]> = {
  PLAN_20: [0, 1, 2, 3, 4],
  PLAN_16: [0, 1, 2, 3],
  PLAN_12: [0, 2, 4],
}

export function localDateInputValue(date = new Date()): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function firstDayOfNextMonthInputValue(date = new Date()): string {
  return localDateInputValue(new Date(date.getFullYear(), date.getMonth() + 1, 1))
}

function parseDateInput(value: string): { year: number; month: number; day: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (!Number.isInteger(year) || month < 1 || month > 12 || day < 1 || day > 31) return null
  return { year, month, day }
}

export function scheduleMonthLabel(value: string): string {
  const parsed = parseDateInput(value)
  return parsed ? `${parsed.year}년 ${parsed.month}월` : '연·월 확인 필요'
}

export function moveScheduleMonth(value: string, offset: number): string {
  const parsed = parseDateInput(value)
  if (!parsed || !Number.isInteger(offset)) return value
  const moved = new Date(Date.UTC(parsed.year, parsed.month - 1 + offset, 1))
  return `${moved.getUTCFullYear()}-${String(moved.getUTCMonth() + 1).padStart(2, '0')}-01`
}

export function countMonthlyPublishDates(activeFrom: string, publishDays: number[]): number {
  const parsed = parseDateInput(activeFrom)
  if (!parsed) return 0

  const uniqueDays = new Set(publishDays)
  const daysInMonth = new Date(Date.UTC(parsed.year, parsed.month, 0)).getUTCDate()
  let count = 0

  for (let day = parsed.day; day <= daysInMonth; day += 1) {
    const jsWeekday = new Date(Date.UTC(parsed.year, parsed.month - 1, day)).getUTCDay()
    const mondayBasedWeekday = (jsWeekday + 6) % 7
    if (uniqueDays.has(mondayBasedWeekday)) count += 1
  }

  return count
}

export function validateScheduleCapacity(
  plan: string,
  publishDays: number[],
  activeFrom: string,
  dailyMax = 1,
  monthTarget: number | null = null,
): string | null {
  const quota = PLAN_TOTALS[plan]
  if (!quota) return '알 수 없는 요금제입니다.'
  // 마케터가 시작 달 편수를 정했으면 그 편수만 놓으면 된다(마케팅 요청 #8).
  const total = monthTarget ?? quota
  if (total === 0) return null
  if (publishDays.length === 0) return '발행 요일을 하나 이상 선택해 주세요.'

  // 사람이 저장한 일정의 시작 달은 계약 편수 그대로다(월 중간 시작이어도 줄이지 않는다).
  const available = countMonthlyPublishDates(activeFrom, publishDays) * dailyMax
  if (available >= total) return null

  const monthLabel = activeFrom.slice(0, 7)
  return `선택한 요일과 하루 최대 ${dailyMax}편으로는 ${monthLabel}에 콘텐츠 항목 ${available}개만 만들 수 있습니다. ${total}개가 필요하므로 발행 요일을 추가하거나 하루 최대 편수를 늘려 주세요.`
}


/**
 * 시작 달 발행 편수(2026-11-21 마케팅 요청 #8). 마케터가 계약 첫 달의 약속 편수를 정한다 — 계약 편수가 상한,
 * 0이면 그 달은 발행 없이 다음 달부터. 서버 `GET …/schedule/month-target`의 값이다.
 */
export interface ScheduleMonthTarget {
  month: string
  contract_quota: number | null
  /** 지금 그 달 약속 편수(시스템 안분·마케터 값·계약 편수 중 지금 유효한 것) */
  month_target: number | null
  /** 마케터가 정해 아직 유효한 값. 없으면 null */
  operator_target: number | null
  /** 계약 첫 달(앞 달에 공개한 글이 없는 달)이라 정할 수 있는가 */
  editable: boolean
}

export const MONTH_TARGET_COPY = {
  label: '시작 달 발행 편수',
  hint: '계약 첫 달에만 정할 수 있습니다. 계약 편수({quota}편)보다 많이 정할 수 없고, 0편이면 그 달은 발행하지 않고 다음 달부터 시작합니다. 줄이면 아직 손대지 않은 항목부터 종료합니다.',
  current: '지금 이 달 약속: {current}편',
  notEditable: '이 달은 계약 첫 달이 아니라 계약 편수를 따릅니다.',
  outOfRange: '시작 달 발행 편수는 0~{quota}편 사이로 정해 주세요.',
} as const

/** "YYYY-MM-DD" 시작일의 달 "YYYY-MM". 형식이 틀리면 null. */
export function scheduleMonthKey(activeFrom: string): string | null {
  return /^\d{4}-\d{2}-\d{2}$/.test(activeFrom) ? activeFrom.slice(0, 7) : null
}

/**
 * 편수를 보내지 않고 저장하면 서버가 쓰는 값 — 마케터가 정한 값이 있으면 그 값, 없으면 계약 편수
 * (2026-11-19 대표 결정: 사람이 일정을 저장하면 시작 달은 계약 편수). 칸은 이 값으로 미리 채운다.
 */
export function defaultMonthTarget(view: ScheduleMonthTarget | null): number | null {
  if (!view) return null
  return view.operator_target ?? view.contract_quota
}

/**
 * 저장 요청에 실을 `month_target`. 정할 수 없는 달이거나 서버가 쓸 값과 같으면 보내지 않는다 —
 * 보내지 않아야 이미 정한 편수·계약 편수 규칙을 서버가 그대로 지킨다.
 */
export function monthTargetPayload(view: ScheduleMonthTarget | null, input: string): number | undefined {
  if (!view?.editable || view.contract_quota === null) return undefined
  const value = Number(input)
  if (input.trim() === '' || !Number.isInteger(value)) return undefined
  return value === defaultMonthTarget(view) ? undefined : value
}

/** 입력값이 0~계약 편수의 정수인가. 정할 수 없는 달이면 언제나 통과(보내지 않는다). */
export function monthTargetError(view: ScheduleMonthTarget | null, input: string): string | null {
  if (!view?.editable || view.contract_quota === null) return null
  const value = Number(input)
  if (input.trim() === '' || !Number.isInteger(value) || value < 0 || value > view.contract_quota) {
    return MONTH_TARGET_COPY.outOfRange.replace('{quota}', String(view.contract_quota))
  }
  return null
}
