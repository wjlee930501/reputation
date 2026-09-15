/**
 * 의료진 목록의 순수 규칙과, 그 목록을 읽어 오는 한 곳.
 *
 * 저장은 집합 전체 교체이므로 표시 순서(`display_order`)는 화면의 줄 순서와 늘 같아야
 * 한다 — 줄을 옮기거나 지운 뒤에는 번호를 다시 매긴다. 대표는 여러 명일 수 있다.
 * 읽어 오는 경로를 여기 한 곳에 두어, 서버가 목록을 다른 응답에 실어 주게 되면
 * 이 함수만 바꾸면 되게 한다.
 */

import { fetchAPI } from './api.ts'
import type { Physician } from '../types/index.ts'

/** 직함 제안. 고르지 않고 직접 적을 수도 있다 — 서버는 자유 문자열을 받는다. */
export const PHYSICIAN_TITLE_OPTIONS: readonly string[] = [
  '대표원장',
  '원장',
  '과장',
  '전문의',
  '진료부장',
]

export function emptyPhysician(displayOrder: number): Physician {
  return {
    name: '',
    title: '',
    specialties: [],
    career: '',
    credentials: null,
    photo_source_id: null,
    display_order: displayOrder,
    is_representative: false,
  }
}

/** 화면의 줄 순서를 표시 순서로 굳힌다. */
export function withDisplayOrder(rows: Physician[]): Physician[] {
  return rows.map((row, index) => ({ ...row, display_order: index }))
}

/** 한 줄을 위/아래로. 끝에서는 아무 일도 하지 않는다. */
export function movePhysician(rows: Physician[], index: number, delta: -1 | 1): Physician[] {
  const target = index + delta
  if (index < 0 || index >= rows.length || target < 0 || target >= rows.length) return rows
  const next = [...rows]
  next[index] = rows[target]
  next[target] = rows[index]
  return withDisplayOrder(next)
}

export function removePhysician(rows: Physician[], index: number): Physician[] {
  return withDisplayOrder(rows.filter((_, i) => i !== index))
}

/** 줄 단위 입력 ↔ 목록. 빈 줄과 앞뒤 공백은 버린다. */
export function parseLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

export function joinLines(values: string[] | null | undefined): string {
  return (values ?? []).join('\n')
}

/** 쉼표로 구분한 전문과목 입력 ↔ 목록. */
export function parseCommaList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

/**
 * 저장할 목록. 이름이 빈 줄은 사람이 추가만 해 두고 채우지 않은 줄이므로 보내지 않는다.
 * 서버가 집합을 통째로 바꾸기 때문에, 빈 줄을 그대로 보내면 이름 없는 의료진이 생긴다.
 */
export function physiciansPayload(rows: Physician[]): Physician[] {
  return withDisplayOrder(rows.filter((row) => row.name.trim() !== '')).map((row) => ({
    ...row,
    name: row.name.trim(),
  }))
}

/** 병원이 아직 한 명도 없으면 빈 줄 하나로 시작한다 — 사람이 바로 입력할 수 있게. */
export function seedPhysicians(rows: Physician[] | null | undefined): Physician[] {
  const list = (rows ?? []).slice().sort((a, b) => a.display_order - b.display_order)
  return list.length > 0 ? withDisplayOrder(list) : [emptyPhysician(0)]
}

export interface DoctorPhotoOption {
  id: string
  title: string
}

interface SourceRowLike {
  id: string
  source_type: string
  title: string
  status: string
}

/** 이 병원의 원장 사진 자료. 제외된 사진은 고를 수 없다. */
export function doctorPhotoOptions(rows: SourceRowLike[]): DoctorPhotoOption[] {
  return rows
    .filter((row) => row.source_type === 'PHOTO_DOCTOR' && row.status !== 'EXCLUDED')
    .map((row) => ({ id: row.id, title: row.title }))
}

/**
 * 의료진 목록을 읽는 유일한 곳.
 *
 * 서버는 지금 `{ hospital_id, physicians }`로 감싸 보낸다. 목록이 다른 응답(병원 상세)에
 * 실려 오게 되더라도 여기만 바꾸면 되도록 두 모양을 모두 받는다.
 */
export async function fetchPhysicians(hospitalId: string): Promise<Physician[]> {
  const payload = await fetchAPI<Physician[] | { physicians?: Physician[] }>(
    `/admin/hospitals/${hospitalId}/physicians`,
  )
  if (Array.isArray(payload)) return payload
  return Array.isArray(payload?.physicians) ? payload.physicians : []
}

export async function fetchDoctorPhotoOptions(hospitalId: string): Promise<DoctorPhotoOption[]> {
  const rows = await fetchAPI<SourceRowLike[]>(`/admin/hospitals/${hospitalId}/essence/sources`)
  return doctorPhotoOptions(Array.isArray(rows) ? rows : [])
}

/** 사진을 쓰는 의료진 이름. 사진 화면이 "누가 쓰는 사진인지"를 말할 때 읽는다. */
export function physicianUsingPhoto(
  rows: Physician[],
  photoSourceId: string,
): Physician | null {
  return rows.find((row) => row.photo_source_id === photoSourceId) ?? null
}
