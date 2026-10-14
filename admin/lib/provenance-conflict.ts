/**
 * 자동 입력 출처 원장의 충돌 한 줄(회색 안내). 서버가 저장한 충돌 값은 칸마다 모양이 다르다 —
 * 진료시간은 요일 객체({mon: "10:00-19:00"}), 지역·키워드는 목록, 진료 항목은 객체 목록, 나머지는
 * 문자열이다. 객체를 React 자식으로 그대로 넘기면 화면 전체가 깨지므로 여기서 문자열로 바꾼다.
 */
import { createElement } from 'react'

import type { FieldProvenance, JsonValue } from '../types/index.ts'
import { ADMIN_COPY } from './admin-copy.ts'
import { DAYS } from './schedule.ts'

const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

export const PROVENANCE_SOURCE_LABEL: Record<string, string> = {
  naver: '네이버',
  homepage: '홈페이지',
  blog: '블로그',
  survey: '설문',
}

function isRecord(value: JsonValue): value is { [key: string]: JsonValue } {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function formatHours(value: { [key: string]: JsonValue }): string | null {
  const parts = DAY_KEYS.flatMap((key, index) => {
    const hours = value[key]
    return typeof hours === 'string' && hours.trim() !== '' ? [`${DAYS[index]} ${hours.trim()}`] : []
  })
  return parts.length > 0 ? parts.join(', ') : null
}

/** 충돌 값 하나를 화면 문자열로. 모르는 모양도 문자열이 되며 예외를 던지지 않는다. */
export function formatConflictValue(value: JsonValue | undefined): string {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value
      .map((item) => (isRecord(item) && typeof item.name === 'string' ? item.name : formatConflictValue(item)))
      .filter((item) => item !== '')
      .join(', ')
  }
  const hours = formatHours(value)
  if (hours !== null) return hours
  return Object.entries(value)
    .map(([key, item]) => `${key} ${formatConflictValue(item)}`.trim())
    .join(', ')
}

/** 회색 안내 문장. 충돌이 없거나 값이 비면 null. */
export function provenanceConflictText(info: FieldProvenance | undefined | null): string | null {
  const conflict = info?.conflict
  if (!conflict) return null
  const value = formatConflictValue(conflict.value)
  if (value === '') return null
  const source = PROVENANCE_SOURCE_LABEL[conflict.source] ?? conflict.source
  return ADMIN_COPY.provenanceConflict.replace('{source}', source).replace('{value}', value)
}

/** 정보 화면의 충돌 한 줄. 확인 절차는 없다(사람의 일이 아니다). */
export function ProvenanceConflict({ info }: { info: FieldProvenance | undefined | null }) {
  const text = provenanceConflictText(info)
  if (text === null) return null
  return createElement(
    'p',
    { 'data-testid': 'provenance-conflict', className: 'mt-1 text-xs text-slate-500' },
    text,
  )
}
