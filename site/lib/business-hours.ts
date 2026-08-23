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

export const WEEKDAY_ORDER = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

export const WEEKDAY_LABELS: Record<string, string> = {
  mon: '월요일',
  tue: '화요일',
  wed: '수요일',
  thu: '목요일',
  fri: '금요일',
  sat: '토요일',
  sun: '일요일',
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

export interface WeeklyHoursRow {
  key: string
  label: string
  /** 운영자가 입력한 그대로의 문구. 값이 없으면 null — 없는 시간을 지어내지 않는다. */
  value: string | null
  closed: boolean
}

/**
 * 요일별 진료시간 표의 행. 항상 월~일 7행을 같은 순서로 낸다.
 *
 * `/visit`의 "진료시간 보기"는 자기 자신을 가리키면서 정작 페이지에는 진료시간 표가
 * 없었다. 이 함수는 JSON-LD(openingHoursSpecification)와 같은 원본(business_hours)에서
 * 화면용 행을 만들어, 구조화 데이터와 사람이 보는 표가 갈리지 않게 한다.
 */
export function buildWeeklyHoursRows(
  hours: Record<string, string> | null | undefined,
): WeeklyHoursRow[] {
  return WEEKDAY_ORDER.map((key) => {
    const raw = hours?.[key]
    const value = typeof raw === 'string' && raw.trim() ? raw.trim() : null
    return {
      key,
      label: WEEKDAY_LABELS[key],
      value,
      closed: value !== null && isClosedLabel(value),
    }
  })
}

export function hasWeeklyHours(hours: Record<string, string> | null | undefined): boolean {
  return buildWeeklyHoursRows(hours).some((row) => row.value !== null)
}

/**
 * 7일 진료시간이 전부 있고 모두 같으며 휴진일이 없는가 — 즉 연중무휴인가.
 *
 * 이런 병원에서 첫 화면이 `오늘 진료 09:00~21:00`과 `토요일 진료 09:00~21:00`을 나란히
 * 놓으면 같은 값을 두 번 말하는 셈이라 정보량이 0이다. 그 자리에 "연중무휴"를 넣으면
 * 환자가 실제로 알고 싶은 사실(주말·공휴일에도 여는가)을 한 칸으로 전달한다.
 */
/**
 * 오늘 다음으로 문 여는 날. 오늘이 휴진일 때 환자가 실제로 궁금해하는 사실이다.
 *
 * 첫 화면 4번째 칸이 `토요일 진료` 고정이라, 일요일에 방문한 환자에게 지나간 토요일
 * 시간을 보여 주고 있었다(S-8). 요일 편차가 있는 병원에서는 그 자리를 "다음 진료"로
 * 바꾼다. 7일을 모두 돌아도 열린 날이 없으면(전부 휴진) null이다.
 */
export function nextOpenDay(
  hours: Record<string, string> | null | undefined,
  todayKey: string,
): { label: string; time: string } | null {
  if (!hours) return null
  const startIndex = WEEKDAY_ORDER.indexOf(todayKey as (typeof WEEKDAY_ORDER)[number])
  if (startIndex < 0) return null
  for (let offset = 1; offset <= 7; offset += 1) {
    const day = WEEKDAY_ORDER[(startIndex + offset) % WEEKDAY_ORDER.length]
    const value = (hours[day] ?? '').trim()
    if (!value || isClosedLabel(value)) continue
    return { label: WEEKDAY_LABELS[day] ?? day, time: value }
  }
  return null
}

export function uniformWeeklyHours(
  hours: Record<string, string> | null | undefined,
): string | null {
  if (!hours) return null
  const values = WEEKDAY_ORDER.map((day) => (hours[day] ?? '').trim())
  if (values.some((value) => !value || isClosedLabel(value))) return null
  const [first] = values
  return values.every((value) => value === first) ? first : null
}

/** 진료시간 표의 페이지 내 앵커. 표가 실제로 존재하는 곳만 가리킨다. */
export const VISIT_HOURS_ANCHOR = 'clinic-hours'

/**
 * "진료시간 보기" 버튼이 가리킬 주소.
 *
 * `onVisitPage`면 같은 페이지의 표로 스크롤한다 — 같은 URL을 다시 여는 링크는
 * 환자에게 아무 정보도 더 주지 않는 막다른 길이다.
 */
export function visitHoursHref(hospitalRootUrl: string, onVisitPage: boolean): string {
  return onVisitPage
    ? `#${VISIT_HOURS_ANCHOR}`
    : `${hospitalRootUrl}/visit#${VISIT_HOURS_ANCHOR}`
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
