/**
 * 병원 정보 탭의 「저장 안 된 변경」 계산(결정 대기 3번 A draft).
 *
 * 탭 하나 안의 PATCH 칸(사실 칸 + 의료진)만 한 번에 저장한다. 사진 업로드·도메인·공개 페이지 칸은
 * 그 칸의 버튼이 바로 저장하므로 여기 세지 않는다. 보내는 본문은 그대로 `factsPatchPayload`다 —
 * 이 계산은 무엇이 바뀌었는지 보여 주기만 하고, 섹션 밖 오래된 값이 덮어쓰지 않는 보호
 * (`profile-patch.ts`)와 사람이 비운 칸의 출처 기록(서버)은 바꾸지 않는다.
 */

import { ADMIN_COPY } from './admin-copy.ts'
import { INFO_SECTION_ORDER, INFO_SECTION_TITLES, type InfoSectionId } from './info-sections.ts'
import { FACTS_PATCH_FIELDS } from './profile-patch.ts'

type FactField = (typeof FACTS_PATCH_FIELDS)[number] | 'physicians'

/** 칸 → 화면 구획. 남은 필수 항목 안내와 같은 구획 이름을 쓴다. */
export const FACT_FIELD_SECTIONS: Readonly<Record<FactField, InfoSectionId>> = {
  physicians: 'director',
  director_philosophy: 'director',
  treatments: 'treatments',
  address: 'contact',
  address_detail: 'contact',
  phone: 'contact',
  business_hours: 'contact',
  website_url: 'channels',
  blog_url: 'channels',
  kakao_channel_url: 'channels',
  google_business_profile_url: 'channels',
  google_maps_url: 'channels',
  naver_place_url: 'channels',
  latitude: 'channels',
  longitude: 'channels',
  wikidata_qid: 'channels',
  gbp_place_id: 'channels',
  naver_place_id: 'channels',
  kakao_place_id: 'channels',
  hira_org_id: 'channels',
  region: 'targeting',
  specialties: 'targeting',
  keywords: 'targeting',
  competitors: 'targeting',
}

/** 빈 값(null·빈 글자·빈 목록·빈 칸만 있는 객체)은 하나로 보고, 객체 키 순서는 무시한다. */
function canonical(value: unknown): unknown {
  if (value === null || value === undefined) return null
  if (typeof value === 'string') return value.trim() === '' ? null : value
  if (Array.isArray(value)) {
    const items = value.map(canonical)
    return items.length === 0 ? null : items
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => [key, canonical(item)] as const)
      .filter(([, item]) => item !== null)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    return entries.length === 0 ? null : Object.fromEntries(entries)
  }
  return value
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b))
}

/** 저장된 값과 지금 폼 값이 다른 칸. `physicians`는 둘 다 읽혔을 때만 비교한다. */
export function changedFactFields(
  saved: Record<string, unknown>,
  current: Record<string, unknown>,
): FactField[] {
  const fields = Object.keys(FACT_FIELD_SECTIONS) as FactField[]
  return fields.filter((field) => {
    if (field === 'physicians' && (saved.physicians === undefined || current.physicians === undefined)) {
      return false
    }
    return !same(saved[field], current[field])
  })
}

/** 바뀐 칸이 있는 구획(화면 순서). */
export function changedSections(fields: readonly FactField[]): InfoSectionId[] {
  const touched = new Set(fields.map((field) => FACT_FIELD_SECTIONS[field]))
  return INFO_SECTION_ORDER.filter((section) => touched.has(section))
}

/** 「모두 저장」 바의 한 줄. */
export function unsavedSummary(sections: readonly InfoSectionId[]): string {
  if (sections.length === 0) return ADMIN_COPY.saveBar.none
  return ADMIN_COPY.saveBar.pending
    .replace('{count}', String(sections.length))
    .replace('{sections}', sections.map((section) => INFO_SECTION_TITLES[section]).join(', '))
}
