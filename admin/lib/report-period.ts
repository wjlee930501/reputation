/** KST 기준 현재 연·월. */
function seoulYearMonth(now: Date): { year: number; month: number } {
  // 리포트 기간은 백엔드가 전부 Asia/Seoul로 계산한다. 브라우저·컨테이너의 로컬
  // 시간대를 쓰면 UTC 환경(Cloud Run 기본)에서 8월 1일 00:30 KST가 아직 7월 31일이라
  // 기본값이 한 달 어긋난다.
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(now)
  const read = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  return { year: read('year'), month: read('month') }
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

/** YYYY-MM 문자열을 연/월로 나눈다. 형식이 어긋나면 null. */
export function parseMonthValue(value: string): { year: number; month: number } | null {
  const match = /^(\d{4})-(\d{2})$/.exec(value.trim())
  if (!match) return null
  const year = Number.parseInt(match[1], 10)
  const month = Number.parseInt(match[2], 10)
  if (month < 1 || month > 12) return null
  return { year, month }
}
