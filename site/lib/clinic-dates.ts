/**
 * 병원 사이트 날짜 표시 — 언제나 한국 시간(Asia/Seoul)으로 그린다.
 *
 * 2026-11-20 마케팅 점검: 서버(Cloud Run)는 UTC라 `toLocaleDateString`이 UTC 날짜를 냈다.
 * 08:00 KST 발행 글(전날 23:00Z)이 하루 전 날짜로 보였고, 관리자 공개일(11.02 08:00)과 화면
 * 발행일(11월 1일)이 어긋났다. 날짜를 그리는 곳은 모두 이 파일의 함수를 쓴다.
 *
 * 표시만 바꾼다 — 구조화 데이터(datePublished·dateModified)·sitemap·llms.txt의 값과
 * 발행일의 의미(published_at vs first_published_at)는 바꾸지 않는다.
 */

const SEOUL = 'Asia/Seoul'

function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function seoulParts(date: Date): { year: string; month: string; day: string } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: SEOUL,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
  }).formatToParts(date)
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? ''
  return { year: get('year'), month: get('month'), day: get('day') }
}

/** 한국 시간 날짜 키(YYYY-MM-DD). 읽을 수 없으면 null. */
export function seoulDateKey(value: string | null | undefined): string | null {
  const date = parse(value)
  if (!date) return null
  const { year, month, day } = seoulParts(date)
  return `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`
}

/** '2026년 11월 2일' — 한국 시간 기준. 읽을 수 없으면 fallback. */
export function formatSeoulDate(value: string | null | undefined, fallback: string): string {
  const date = parse(value)
  if (!date) return fallback
  const { year, month, day } = seoulParts(date)
  return `${year}년 ${month}월 ${day}일`
}

/** '2026.11.02' — 한국 시간 기준(레이아웃 C 새 소식 목록). 읽을 수 없으면 ''. */
export function formatSeoulDotDate(value: string | null | undefined): string {
  const key = seoulDateKey(value)
  return key ? key.replace(/-/g, '.') : ''
}

export interface ArticleDateLabels {
  /** 발행 표시 글자와 그 값(<time dateTime>). */
  published: { label: string; value: string | undefined }
  /** 최근 업데이트 — 발행 날짜보다 뒤일 때만. 같은 날이거나 앞이면 null. */
  updated: { label: string; value: string } | null
}

/**
 * 글 상세의 발행·최근 업데이트 표시.
 *
 * `body_updated_at`은 발행 전 편집(예약 글을 고친 시각)에도 찍힌다. 그래서 '발행 11월 1일 ·
 * 최근 업데이트 10월 30일'처럼 업데이트가 발행보다 앞서 보였다. 화면에는 발행 날짜보다 뒤인
 * 업데이트만 싣는다.
 */
export function articleDateLabels(content: {
  published_at: string | null
  scheduled_date: string | null
  body_updated_at: string | null
}): ArticleDateLabels {
  const published = {
    label: formatSeoulDate(content.published_at, content.scheduled_date ?? ''),
    value: content.published_at ?? content.scheduled_date ?? undefined,
  }
  const updatedKey = seoulDateKey(content.body_updated_at)
  const publishedKey = seoulDateKey(content.published_at) ?? content.scheduled_date ?? null
  const updated =
    updatedKey && content.body_updated_at && (!publishedKey || updatedKey > publishedKey)
      ? { label: formatSeoulDate(content.body_updated_at, ''), value: content.body_updated_at }
      : null
  return { published, updated }
}
