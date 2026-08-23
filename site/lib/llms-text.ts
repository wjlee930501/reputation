import { buildWeeklyHoursRows } from './business-hours.ts'

const MARKDOWN_LINK_CHARS = /[\[\]()]/g
const CONTROL_OR_LINE_BREAK = /[\u0000-\u001f\u007f]+/g
const SPACES = /\s+/g

export function llmsTextValue(value: string | null | undefined): string {
  return (value || '')
    .replace(CONTROL_OR_LINE_BREAK, ' ')
    .replace(MARKDOWN_LINK_CHARS, '')
    .replace(SPACES, ' ')
    .trim()
}

/**
 * llms.txt의 요일별 진료시간 블록 (P-A-6).
 *
 * JSON-LD(openingHoursSpecification)와 `/visit`의 진료시간 표는 이미
 * `business_hours` 하나를 읽는데, llms.txt에는 진료시간이 아예 없었다. 답변 엔진이
 * 가장 자주 받는 질문이 "지금 여나요"인데도 이 파일만 그 사실을 담지 않고 있었다.
 *
 * 같은 원본에서 같은 파서(buildWeeklyHoursRows)로 만들기 때문에 화면·구조화
 * 데이터·llms.txt가 서로 다른 시간을 말할 수 없다. 값이 없는 요일은 지어내지 않고
 * 건너뛴다. 모든 요일이 비어 있으면 블록 자체를 만들지 않는다.
 */
export function llmsBusinessHoursLines(
  hours: Record<string, string> | null | undefined,
): string[] {
  const rows = buildWeeklyHoursRows(hours).filter((row) => row.value !== null)
  if (rows.length === 0) return []
  return [
    '## 진료시간',
    ...rows.map((row) => `- ${row.label}: ${llmsTextValue(row.value)}`),
    '',
  ]
}

export function llmsUrlValue(value: string | null | undefined): string | null {
  const firstLine = (value || '').split(/\r?\n/, 1)[0]?.trim()
  if (!firstLine) return null
  try {
    const url = new URL(firstLine)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}
