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

/**
 * 아직 사람이 정하지 않은 브랜드 항목은 "승인 필요"가 아니라 "기본값 사용 중"이다.
 *
 * 로고·대표색·첫 화면 문구는 값이 없어도 공개 페이지가 병원명 워드마크와 기본 색으로
 * 정상 노출한다. 사람이 하는 일은 승인이 아니라 override뿐이므로, 비어 있다는 사실만
 * 알리고 승인 버튼이나 미승인 배지를 만들지 않는다.
 */
export function brandDefaultsNotice(missingLabels: string[]): string | null {
  if (missingLabels.length === 0) return null
  return `공개 페이지 기본값 사용 중: ${missingLabels.join(', ')} — 바꾸려면 입력`
}

/** 사진 자료 유형. 업로드 폼의 선택지이자 목록 필터의 기준이다. */
export const PHOTO_SOURCE_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'PHOTO_DOCTOR', label: '사진 — 원장' },
  { value: 'PHOTO_CLINIC_EXTERIOR', label: '사진 — 외관' },
  { value: 'PHOTO_CLINIC_INTERIOR', label: '사진 — 내부' },
  { value: 'PHOTO_TREATMENT_ROOM', label: '사진 — 진료/시술실' },
]

export function isPhotoSourceType(sourceType: string): boolean {
  return PHOTO_SOURCE_TYPE_OPTIONS.some((option) => option.value === sourceType)
}

/**
 * 허용 용도는 사진 유형에서 나온다 — 사람에게 다시 묻지 않는다.
 *
 * 원장 사진은 실제 인물 확인이 전제이므로 의료진 영역에, 나머지 공간 사진은 hero·갤러리에
 * 쓴다. 생성 이미지는 애초에 이 화면의 업로드 대상이 아니다.
 */
export function assetKindForPhotoType(sourceType: string): string {
  return sourceType === 'PHOTO_DOCTOR' ? 'VERIFIED_REAL_PERSON' : 'VERIFIED_FACILITY'
}

export interface PhotoUploadDraft {
  sourceType: string
  title: string
  file: Blob
  isPublic: boolean
  owner: string
  basis: string
  reference: string
}

/**
 * 사진 한 장의 업로드 본문. 공개 여부와 권리 3필드를 같은 요청에 실어야 서버가
 * 저장과 동시에 공개할 수 있다 — 나중에 다시 공개를 켜는 단계를 만들지 않는다.
 */
export function photoUploadFormData(draft: PhotoUploadDraft): FormData {
  const body = new FormData()
  body.append('source_type', draft.sourceType)
  body.append('title', draft.title.trim())
  body.append('file', draft.file)
  body.append('is_public', draft.isPublic ? 'true' : 'false')
  body.append('asset_kind', assetKindForPhotoType(draft.sourceType))
  body.append('photo_source_owner', draft.owner.trim())
  body.append('photo_rights_basis', draft.basis)
  body.append('photo_evidence_reference', draft.reference.trim())
  return body
}
