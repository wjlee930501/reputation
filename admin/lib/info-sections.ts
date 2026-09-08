/**
 * 병원 정보 화면의 섹션 순서와, 서버가 내려준 남은 필수 항목을 섹션에 붙이는 순수 함수.
 *
 * 완료 판정은 서버가 한다(`profile_complete` 파생). 화면은 서버가 준
 * `missing_profile_requirements`를 그대로 세어 보여 주고, 사람이 바로 그 칸으로
 * 갈 수 있게 섹션 앵커만 계산한다.
 */

import type { MissingProfileRequirement } from '@/types'

export type { MissingProfileRequirement }

export type InfoSectionId = 'director' | 'treatments' | 'contact' | 'channels' | 'targeting'

/** 화면에 나오는 순서. */
export const INFO_SECTION_ORDER: InfoSectionId[] = [
  'director',
  'treatments',
  'contact',
  'channels',
  'targeting',
]

export const INFO_SECTION_TITLES: Record<InfoSectionId, string> = {
  director: '병원·원장',
  treatments: '진료 항목',
  contact: '연락처·진료시간',
  channels: '공식 채널',
  targeting: '운영 기준 정보',
}

const REQUIREMENT_SECTIONS: Record<string, InfoSectionId> = {
  director_basic: 'director',
  director_philosophy: 'director',
  contact: 'contact',
  web_channels: 'channels',
  ai_channels: 'channels',
  geo: 'channels',
  targeting: 'targeting',
  treatments: 'treatments',
}

/** 모르는 키는 첫 섹션으로 보낸다 — 링크가 아무 데도 가지 않는 것보다 낫다. */
export function sectionForRequirement(key: string): InfoSectionId {
  return REQUIREMENT_SECTIONS[key] ?? 'director'
}

/** 섹션 앵커 id. 남은 항목 링크의 목적지. */
export function infoSectionAnchorId(section: InfoSectionId): string {
  return `info-${section}`
}

/** 남은 필수 항목을 한 줄로. 사람이 세지 않아도 되게 개수를 먼저 말한다. */
export function remainingRequirementsSummary(items: MissingProfileRequirement[]): string {
  if (items.length === 0) return '필수 항목 입력 완료'
  return `남은 필수 항목 ${items.length}개 · ${items.map((item) => item.label).join(', ')}`
}
