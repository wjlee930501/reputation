// 진료시간(business_hours JSON) → schema.org OpeningHoursSpecification 변환 공통 유틸.
// 허브(JSON-LD MedicalClinic)와 /visit 페이지가 동일한 파서를 사용해
// opens/closes가 있는 구조화 데이터를 일관되게 내보낸다.

export const SCHEMA_DAY_OF_WEEK: Record<string, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
}

const CLOSED_KEYWORDS = ['휴진', '휴무', 'closed']

export function isClosedLabel(value: string): boolean {
  const lowered = value.toLowerCase()
  return CLOSED_KEYWORDS.some((kw) => lowered.includes(kw))
}

export function extractTimeRanges(value: string): Array<{ opens: string; closes: string }> {
  const ranges: Array<{ opens: string; closes: string }> = []
  // "08:30-18:00 (13:00-14:00 점심)"에서 점심 종료 14:00를 진료 종료로
  // 오인하지 않는다. 휴게시간 괄호만 제거하고 실제 오전/오후 분할 진료 표기는 유지한다.
  const withoutBreakNotes = value.replace(
    /\([^)]*(?:점심|휴게|브레이크|break)[^)]*\)/gi,
    ' ',
  )
  for (const segment of withoutBreakNotes.split(/[,/]|·|및|그리고/)) {
    const trimmed = segment.trim()
    if (!trimmed || isClosedLabel(trimmed)) continue
    const matches = trimmed.match(/\d{1,2}:\d{2}/g)
    if (matches && matches.length >= 2) {
      ranges.push({ opens: matches[0], closes: matches[matches.length - 1] })
    }
  }
  return ranges
}

export function buildOpeningHoursSpec(hours: Record<string, string> | null | undefined) {
  if (!hours) return []
  const specs: Array<Record<string, unknown>> = []
  for (const [day, rawValue] of Object.entries(hours)) {
    const value = String(rawValue ?? '')
    const dayOfWeek = SCHEMA_DAY_OF_WEEK[day.toLowerCase()] || day
    if (isClosedLabel(value)) {
      specs.push({
        '@type': 'OpeningHoursSpecification',
        dayOfWeek,
        description: value,
        opens: '00:00',
        closes: '00:00',
      })
      continue
    }
    const ranges = extractTimeRanges(value)
    if (ranges.length === 0) {
      specs.push({
        '@type': 'OpeningHoursSpecification',
        dayOfWeek,
        description: value,
      })
      continue
    }
    for (const range of ranges) {
      specs.push({
        '@type': 'OpeningHoursSpecification',
        dayOfWeek,
        description: value,
        opens: range.opens,
        closes: range.closes,
      })
    }
  }
  return specs
}
