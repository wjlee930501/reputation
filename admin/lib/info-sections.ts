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

/** 텍스트 자료 유형. 업로드 폼의 선택지이자 근거 자료 표의 기준이다. */
export const TEXT_SOURCE_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'HOMEPAGE', label: '병원 홈페이지' },
  { value: 'NAVER_BLOG', label: '네이버 블로그' },
  { value: 'YOUTUBE', label: '유튜브' },
  { value: 'INTERVIEW', label: '원장 인터뷰지' },
  { value: 'LANDING_PAGE', label: '랜딩 페이지' },
  { value: 'BROCHURE', label: '브로슈어' },
  { value: 'INTERNAL_NOTE', label: '내부 메모' },
  { value: 'OTHER', label: '기타' },
]

/** 사진은 사진 섹션이 다룬다 — 근거 자료 표는 본문이 있는 자료만 센다. */
export function isTextSource(source: { source_type: string }): boolean {
  return !source.source_type.startsWith('PHOTO_')
}

export type SourceRowTone = 'neutral' | 'good' | 'paused' | 'warn'

/*
  처리는 자동이다. 오류도 자동 재시도 대상이므로 사람이 눌러야 할 버튼이 아니라
  현재 상태로만 말한다. 실패 원인(process_error)은 여기 붙이지 않는다 — 사람이 손대야
  하는 사건이라면 현황 화면의 인시던트가 따로 알린다.
*/
const SOURCE_ROW_STATUS: Record<string, { label: string; tone: SourceRowTone }> = {
  PENDING: { label: '처리 대기 (자동)', tone: 'neutral' },
  PROCESSED: { label: '처리 완료', tone: 'good' },
  EXCLUDED: { label: '제외', tone: 'paused' },
  ERROR: { label: '처리 실패 — 자동 재시도 중', tone: 'warn' },
}

export function sourceRowStatus(
  source: { status: string },
): { label: string; tone: SourceRowTone } {
  return SOURCE_ROW_STATUS[source.status] ?? { label: '처리 상태 확인 필요', tone: 'neutral' }
}

/** 근거 노트 분류 라벨과 표시 순서. 자료 화면과 병원 자료 화면이 같은 값을 쓴다. */
export const NOTE_TYPE_LABELS: Record<string, string> = {
  KEY_MESSAGE: '핵심 메시지',
  TONE_SIGNAL: '말투 기준',
  TREATMENT_SIGNAL: '진료 설명 근거',
  RISK_SIGNAL: '주의 표현',
  PATIENT_PROMISE: '환자 약속',
  DOCTOR_PHILOSOPHY: '의료진 철학',
  LOCAL_CONTEXT: '지역 맥락',
  PROOF_POINT: '근거 자료',
  CONFLICT: '상충 메모',
}

export const NOTE_GROUP_ORDER: string[] = [
  'DOCTOR_PHILOSOPHY',
  'PATIENT_PROMISE',
  'KEY_MESSAGE',
  'TREATMENT_SIGNAL',
  'TONE_SIGNAL',
  'PROOF_POINT',
  'LOCAL_CONTEXT',
  'RISK_SIGNAL',
  'CONFLICT',
]

/** 모르는 분류는 버리지 않고 알려진 순서 뒤에 붙인다. */
export function groupNotesByType<T extends { note_type: string }>(
  notes: T[],
): Array<{ noteType: string; label: string; notes: T[] }> {
  const grouped = new Map<string, T[]>()
  for (const note of notes) {
    const list = grouped.get(note.note_type) ?? []
    list.push(note)
    grouped.set(note.note_type, list)
  }
  const known = NOTE_GROUP_ORDER.filter((type) => grouped.has(type))
  const extras = [...grouped.keys()].filter((type) => !NOTE_GROUP_ORDER.includes(type))
  return [...known, ...extras].map((noteType) => ({
    noteType,
    label: NOTE_TYPE_LABELS[noteType] ?? noteType,
    notes: grouped.get(noteType) ?? [],
  }))
}

export interface SourceProcessingRunSummary {
  state: string | null
  total_count: number
}

const ACTIVE_RUN_STATES = new Set(['REQUESTED', 'QUEUED', 'RUNNING'])

/**
 * 진행 중인 처리만 한 줄로 알린다. 끝난 처리는 표의 상태가 이미 말하므로 다시 쓰지 않고,
 * 사람이 시작하거나 멈출 것이 없으므로 버튼도 만들지 않는다.
 */
export function processingSummary(run: SourceProcessingRunSummary | null): string | null {
  if (!run || !run.state || !ACTIVE_RUN_STATES.has(run.state)) return null
  if (run.total_count <= 0) return null
  return `자동 처리 중 ${run.total_count}건`
}
