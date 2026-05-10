export const DAYS = ['월', '화', '수', '목', '금', '토', '일'] as const

export const PLAN_TOTALS: Record<string, number> = {
  PLAN_16: 16,
  PLAN_12: 12,
  PLAN_8: 8,
}

export const DEFAULT_PUBLISH_DAYS_BY_PLAN: Record<string, number[]> = {
  PLAN_16: [0, 1, 2, 3],
  PLAN_12: [0, 2, 4],
  PLAN_8: [1, 4],
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
): string | null {
  const total = PLAN_TOTALS[plan]
  if (!total) return '알 수 없는 요금제입니다.'
  if (publishDays.length === 0) return '발행 요일을 하나 이상 선택해 주세요.'

  const available = countMonthlyPublishDates(activeFrom, publishDays)
  if (available >= total) return null

  const monthLabel = activeFrom.slice(0, 7)
  return `선택한 요일로는 ${monthLabel}에 ${available}개 슬롯만 생성됩니다. ${total}개 슬롯이 필요하므로 발행 요일을 추가하거나 요금제를 변경해 주세요.`
}
