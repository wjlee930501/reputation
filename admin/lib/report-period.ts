/** KST 기준 현재 연·월·일·시·분. */
function seoulParts(now: Date): {
  year: number
  month: number
  day: number
  hour: number
  minute: number
} {
  // 리포트 기간은 백엔드가 전부 Asia/Seoul로 계산한다. 브라우저·컨테이너의 로컬
  // 시간대를 쓰면 UTC 환경(Cloud Run 기본)에서 8월 1일 00:30 KST가 아직 7월 31일이라
  // 기본값이 한 달 어긋난다.
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now)
  const read = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  return {
    year: read('year'),
    month: read('month'),
    day: read('day'),
    hour: read('hour'),
    minute: read('minute'),
  }
}

/** KST 기준 현재 연·월. */
function seoulYearMonth(now: Date): { year: number; month: number } {
  const { year, month } = seoulParts(now)
  return { year, month }
}

/** `<input type="month">`가 쓰는 YYYY-MM 형식으로 KST 기준 지난달을 돌려준다.
 *
 * 월간 리포트 수동 생성의 기본값 — 월말 배치 실패는 대개 달이 바뀐 뒤에 발견되므로
 * '이번 달'이 아니라 '지난달'이 맞다. 1월에는 전년 12월로 넘어가야 한다.
 */
export function previousMonthValue(now: Date = new Date()): string {
  const { year, month } = seoulYearMonth(now)
  const previousMonth = month === 1 ? 12 : month - 1
  const previousYear = month === 1 ? year - 1 : year
  return `${previousYear}-${String(previousMonth).padStart(2, '0')}`
}

/** YYYY-MM 형식으로 KST 기준 이번 달. 선택 목록의 가장 늦은 달이다. */
export function currentMonthValue(now: Date = new Date()): string {
  const { year, month } = seoulYearMonth(now)
  return `${year}-${String(month).padStart(2, '0')}`
}

/** YYYY-MM 문자열을 연/월로 나눈다. 형식이 어긋나면 null. */
export function parseMonthValue(value: string): { year: number; month: number } | null {
  const match = /^(\d{4})-(\d{2})$/.exec(value.trim())
  if (!match) return null
  const year = Number.parseInt(match[1], 10)
  const month = Number.parseInt(match[2], 10)
  if (month < 1 || month > 12) return null
  return { year, month }
}

/** 선택 가능한 첫 연도. 그 이전 달은 만들 이력이 없다. */
export const FIRST_REPORT_YEAR = 2020

export type ReportMonthBlockReason = 'NOT_CLOSED' | 'FUTURE'

export interface ReportMonthOption {
  readonly month: number
  readonly selectable: boolean
  /** 고를 수 없는 이유. 고를 수 있으면 null. */
  readonly reason: ReportMonthBlockReason | null
  /** 선택 목록에 그대로 쓰는 라벨. 이유가 있으면 괄호로 함께 알린다. */
  readonly label: string
}

const REASON_LABEL: Record<ReportMonthBlockReason, string> = {
  NOT_CLOSED: '마감 후 생성',
  FUTURE: '아직 오지 않은 달',
}

export const REPORT_MONTH_BLOCK_MESSAGE: Record<ReportMonthBlockReason, string> = {
  NOT_CLOSED:
    '아직 마감되지 않은 달입니다. 다음 달 1일 00시 15분(KST) 이후에 생성할 수 있습니다.',
  FUTURE: '아직 오지 않은 달입니다. 지난달까지의 마감된 월만 생성할 수 있습니다.',
}

/**
 * 백엔드 `require_closed_period`와 같은 경계를 화면에서도 판정한다.
 *
 * 월간 리포트는 그 달이 마감된 뒤에만 만들 수 있다(다음 달 1일 00시 15분 KST).
 * 마감 전에 빈 리포트 행을 만들면 월말 배치가 dedupe로 영구 차단된다.
 */
export function reportMonthBlockReason(
  period: { year: number; month: number },
  now: Date = new Date(),
): ReportMonthBlockReason | null {
  const { year, month, day, hour, minute } = seoulParts(now)
  const closeYear = period.month === 12 ? period.year + 1 : period.year
  const closeMonth = period.month === 12 ? 1 : period.month + 1
  const nowKey = [year, month, day, hour, minute]
  const closeKey = [closeYear, closeMonth, 1, 0, 15]
  for (let index = 0; index < closeKey.length; index += 1) {
    if (nowKey[index] !== closeKey[index]) {
      if (nowKey[index] > closeKey[index]) return null
      // 마감 전이다. 이번 달을 넘어선 달은 "마감 전"이 아니라 "아직 오지 않은 달"이다.
      return period.year > year || (period.year === year && period.month > month)
        ? 'FUTURE'
        : 'NOT_CLOSED'
    }
  }
  return null
}

/**
 * 한 해의 12개월 선택 항목. 이번 달은 목록에서 빠지지 않고 이유와 함께 남는다.
 *
 * 이전 구현은 상한을 '지난달'로 잡아 이번 달 옵션 자체를 지웠고, 8월에 8월을
 * 고를 수 없다는 사실만 보이고 이유는 어디에도 없었다.
 */
export function reportMonthOptions(year: number, now: Date = new Date()): ReportMonthOption[] {
  return Array.from({ length: 12 }, (_, index) => index + 1).map((month) => {
    const reason = reportMonthBlockReason({ year, month }, now)
    return {
      month,
      selectable: reason === null,
      reason,
      label: reason === null ? `${month}월` : `${month}월 (${REASON_LABEL[reason]})`,
    }
  })
}

/** 최신 연도부터 내려오는 선택 가능한 연도 목록. 이번 연도는 항상 들어간다. */
export function reportYearOptions(now: Date = new Date()): number[] {
  const { year } = seoulYearMonth(now)
  const span = Math.max(1, year - FIRST_REPORT_YEAR + 1)
  return Array.from({ length: span }, (_, index) => year - index)
}
