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

/**
 * 공휴일 진료시간 키(2026-11-18 마케팅 요청 — 365서귀포삼성정형외과 원장님: 병원 이름의 '365' 때문에
 * AI가 연중무휴로 오해한다). 요일 키와 같은 `business_hours` 안의 문자열 하나다 — '휴진' 또는
 * '09:00 ~ 13:00'. 요일 순서(WEEKDAY_ORDER)에는 넣지 않는다: 오늘·다음 진료·주말 칸은 요일
 * 계산이고, 오늘이 공휴일인지는 달력이 없어 모른다. 값이 없으면 모든 출력이 도입 전과 같다.
 */
export const HOLIDAY_KEY = 'holiday'
export const HOLIDAY_LABEL = '공휴일'
export const SCHEMA_PUBLIC_HOLIDAYS = 'https://schema.org/PublicHolidays'

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

/** 공휴일 진료시간. 값이 없거나 빈 문자열이면 null — 공휴일 정보를 지어내지 않는다. */
export function holidayHours(
  hours: Record<string, string> | null | undefined,
): { value: string; closed: boolean } | null {
  const raw = hours?.[HOLIDAY_KEY]
  const value = typeof raw === 'string' ? raw.trim() : ''
  if (!value) return null
  return { value, closed: isClosedLabel(value) }
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
 * 요일별 진료시간 표의 행. 항상 월~일 7행을 같은 순서로 내고, 공휴일 값이 있으면 그 뒤에
 * '공휴일' 행을 하나 더 붙인다(없으면 7행 그대로).
 *
 * `/visit`의 "진료시간 보기"는 자기 자신을 가리키면서 정작 페이지에는 진료시간 표가
 * 없었다. 이 함수는 JSON-LD(openingHoursSpecification)와 같은 원본(business_hours)에서
 * 화면용 행을 만들어, 구조화 데이터와 사람이 보는 표가 갈리지 않게 한다.
 */
export function buildWeeklyHoursRows(
  hours: Record<string, string> | null | undefined,
): WeeklyHoursRow[] {
  const rows: WeeklyHoursRow[] = WEEKDAY_ORDER.map((key) => {
    const raw = hours?.[key]
    const value = typeof raw === 'string' && raw.trim() ? raw.trim() : null
    return {
      key,
      label: WEEKDAY_LABELS[key],
      value,
      closed: value !== null && isClosedLabel(value),
    }
  })
  const holiday = holidayHours(hours)
  if (holiday) {
    rows.push({ key: HOLIDAY_KEY, label: HOLIDAY_LABEL, value: holiday.value, closed: holiday.closed })
  }
  return rows
}

/** 요일 진료시간이 하나라도 있는가. 공휴일 값만으로는 '진료시간이 있다'고 보지 않는다. */
export function hasWeeklyHours(hours: Record<string, string> | null | undefined): boolean {
  return buildWeeklyHoursRows(hours).some((row) => row.key !== HOLIDAY_KEY && row.value !== null)
}

/**
 * 휴진 안내 문장의 요일 부분. 요일은 짧은 이름(`월`·`일`)으로 받아 종전처럼 `일요일`로 잇고,
 * 공휴일 휴진이면 `, 공휴일`을 덧붙인다 — '공휴일요일'이 되지 않게. 공휴일 휴진이 아니면
 * 도입 전과 글자가 같다. 둘 다 없으면 null.
 */
export function closedDaysPhrase(shortDayLabels: string[], holidayClosed: boolean): string | null {
  const parts: string[] = []
  if (shortDayLabels.length > 0) parts.push(`${shortDayLabels.join(', ')}요일`)
  if (holidayClosed) parts.push(HOLIDAY_LABEL)
  return parts.length > 0 ? parts.join(', ') : null
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
  // 공휴일 휴진인 병원은 연중무휴가 아니다(2026-11-18 — '365' 병원이 연중무휴로 오해받던 문제).
  if (holidayHours(hours)?.closed) return null
  return sameHoursEveryDay(hours)
}

function sameHoursEveryDay(hours: Record<string, string>): string | null {
  const values = WEEKDAY_ORDER.map((day) => (hours[day] ?? '').trim())
  if (values.some((value) => !value || isClosedLabel(value))) return null
  const [first] = values
  return values.every((value) => value === first) ? first : null
}

/**
 * 첫 화면 넷째 칸을 '휴무일'로 쓸 때의 값. 7일 모두 같은 시간에 열면
 * - 공휴일 휴진이면 `공휴일` — 연중무휴라고 쓰지 않는다,
 * - 그 밖(공휴일 진료 또는 값 없음)이면 종전처럼 `연중무휴`.
 * 요일마다 시간이 다르거나 쉬는 요일이 있으면 null(호출부가 다음 진료·주말 칸을 쓴다).
 */
export function weeklyRestDayLabel(hours: Record<string, string> | null | undefined): string | null {
  if (!hours || sameHoursEveryDay(hours) === null) return null
  return holidayHours(hours)?.closed ? HOLIDAY_LABEL : '연중무휴'
}

/**
 * 첫 화면의 주말 진료 칸. 토요일만 보여 주면 일요일에도 여는 병원이 그 사실을 말하지 못한다
 * (2026-11-17 마케팅 요청 — 365서귀포삼성정형외과는 토·일 같은 시간에 진료한다).
 *
 * - 토·일 모두 진료하고 시간이 글자까지 같으면 `토·일요일 진료` 한 줄.
 * - 둘 다 진료하지만 시간이 다르면 `토·일요일 진료`에 `토 …` / `일 …`을 함께 싣는다.
 * - 일요일이 비었거나 휴진이면 종전처럼 `토요일 진료`와 토요일 값(휴진 표기 포함).
 * - 토요일이 비었거나 휴진인데 일요일만 열면 `일요일 진료`.
 * - 둘 다 비었으면 `토요일 진료`에 값 없음(null) — 호출부가 '방문 전 전화 확인'을 쓴다.
 */
export function weekendHoursCard(
  hours: Record<string, string> | null | undefined,
): { label: string; value: string | null; sunday: string | null } {
  const sat = (hours?.sat ?? '').trim()
  const sun = (hours?.sun ?? '').trim()
  const satOpen = Boolean(sat) && !isClosedLabel(sat)
  const sunOpen = Boolean(sun) && !isClosedLabel(sun)
  if (satOpen && sunOpen) {
    return sat === sun
      ? { label: '토·일요일 진료', value: sat, sunday: null }
      : { label: '토·일요일 진료', value: sat, sunday: sun }
  }
  if (!satOpen && sunOpen) return { label: '일요일 진료', value: sun, sunday: null }
  return { label: '토요일 진료', value: sat || null, sunday: null }
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

/**
 * schema.org OpeningHoursSpecification 목록. 공휴일(`holiday`)은 `dayOfWeek`를
 * `https://schema.org/PublicHolidays`로 낸다 — 휴진이면 다른 휴진 요일처럼 00:00~00:00.
 * 공휴일 값이 비어 있으면 항목을 만들지 않는다(도입 전과 같은 출력).
 */
export function buildOpeningHoursSpec(hours: Record<string, string> | null | undefined) {
  if (!hours) return []
  const specs: Array<Record<string, unknown>> = []
  for (const [day, rawValue] of Object.entries(hours)) {
    const value = String(rawValue ?? '')
    const isHoliday = day.toLowerCase() === HOLIDAY_KEY
    if (isHoliday && !value.trim()) continue
    const dayOfWeek = isHoliday
      ? SCHEMA_PUBLIC_HOLIDAYS
      : SCHEMA_DAY_OF_WEEK[day.toLowerCase()] || day
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
