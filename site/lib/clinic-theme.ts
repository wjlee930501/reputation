import type { CSSProperties } from 'react'

import { resolveAssetUrl, type Hospital, type HospitalPhysician } from './hospital-payload.ts'

export const DEFAULT_CLINIC_PRIMARY = '#17365D'

export const CLINIC_INK = '#0A1B2F'
const CLINIC_PAPER = '#FBFAF7'
const WHITE = '#FFFFFF'
const HEX_COLOR = /^#[0-9a-f]{6}$/i

/** 채워진 표면·본문급 텍스트에 쓰는 단계는 종이 대비 7:1을 넘긴다. */
const STRONG_CONTRAST = 7
/** 링크·CTA 단계는 종이 대비 WCAG AA 4.5:1을 넘긴다. */
const ACTION_CONTRAST = 4.5

type Rgb = Readonly<{ red: number; green: number; blue: number }>
type ThemeStyle = CSSProperties & Record<`--clinic-${string}`, string>

export function normalizeClinicColor(value: string | null | undefined, fallback: string): string {
  const color = (value || '').trim()
  return HEX_COLOR.test(color) ? color.toUpperCase() : fallback
}

function parseHex(color: string): Rgb | null {
  if (!HEX_COLOR.test(color)) return null
  return {
    red: Number.parseInt(color.slice(1, 3), 16),
    green: Number.parseInt(color.slice(3, 5), 16),
    blue: Number.parseInt(color.slice(5, 7), 16),
  }
}

function toHex({ red, green, blue }: Rgb): string {
  const channel = (value: number) => Math.max(0, Math.min(255, Math.round(value)))
    .toString(16)
    .padStart(2, '0')
  return `#${channel(red)}${channel(green)}${channel(blue)}`.toUpperCase()
}

function mixHex(color: string, target: string, targetWeight: number): string {
  const sourceRgb = parseHex(color)
  const targetRgb = parseHex(target)
  if (!sourceRgb || !targetRgb) return color
  const weight = Math.max(0, Math.min(1, targetWeight))
  return toHex({
    red: sourceRgb.red * (1 - weight) + targetRgb.red * weight,
    green: sourceRgb.green * (1 - weight) + targetRgb.green * weight,
    blue: sourceRgb.blue * (1 - weight) + targetRgb.blue * weight,
  })
}

function contrastSafeColor(color: string, background: string, minimumRatio: number): string {
  if (contrastRatio(color, background) >= minimumRatio) return color
  for (let step = 1; step <= 20; step += 1) {
    const candidate = mixHex(color, CLINIC_INK, step / 20)
    if (contrastRatio(candidate, background) >= minimumRatio) return candidate
  }
  return CLINIC_INK
}

function relativeLuminance(color: string): number {
  const rgb = parseHex(color)
  if (!rgb) return 0
  const linearize = (channel: number) => {
    const value = channel / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  }
  return (
    linearize(rgb.red) * 0.2126 +
    linearize(rgb.green) * 0.7152 +
    linearize(rgb.blue) * 0.0722
  )
}

export function contrastRatio(first: string, second: string): number {
  const lighter = Math.max(relativeLuminance(first), relativeLuminance(second))
  const darker = Math.min(relativeLuminance(first), relativeLuminance(second))
  return (lighter + 0.05) / (darker + 0.05)
}

function readableForeground(background: string): string {
  return contrastRatio(background, WHITE) >= contrastRatio(background, CLINIC_INK)
    ? WHITE
    : CLINIC_INK
}

/**
 * 승인된 대표색 하나에서 병원 표면 전체가 쓰는 단계를 파생한다.
 *
 * AE는 로고와 대표색 하나만 승인하고, 밝기 단계와 대비 안전 색상은 여기서 만든다.
 * 병원별로 임의의 색을 페이지에 직접 뿌리지 않기 위해 CSS는 이 토큰만 참조한다.
 */
export function buildClinicThemeStyle(
  hospital: Pick<Hospital, 'brand_primary_color' | 'brand_accent_color'>,
): ThemeStyle {
  const brand = normalizeClinicColor(hospital.brand_primary_color, DEFAULT_CLINIC_PRIMARY)
  const action = contrastSafeColor(brand, CLINIC_PAPER, ACTION_CONTRAST)
  const strong = contrastSafeColor(brand, CLINIC_PAPER, STRONG_CONTRAST)
  const hover = mixHex(action, CLINIC_INK, 0.14)
  const soft = mixHex(brand, CLINIC_PAPER, 0.88)
  const tint = mixHex(brand, CLINIC_PAPER, 0.94)
  const veil = mixHex(brand, CLINIC_PAPER, 0.62)
  const edge = mixHex(brand, CLINIC_PAPER, 0.45)
  const line = mixHex(brand, CLINIC_PAPER, 0.78)

  // 두 번째 브랜드 색을 새로 만들지 않는다. 승인된 accent가 없으면 대표색에서 파생한다.
  const accent = normalizeClinicColor(hospital.brand_accent_color, brand)
  const focus = contrastRatio(accent, CLINIC_PAPER) >= 3 ? accent : strong
  // 승인된 accent를 글자에 쓰려면 종이 대비를 통과해야 한다. 예를 들어 gold
  // #B79045는 흰 종이에서 3.2:1로 본문급 대비에 미달하므로 그대로 쓸 수 없다.
  const accentStrong = contrastSafeColor(accent, CLINIC_PAPER, ACTION_CONTRAST)

  return {
    '--clinic-brand': brand,
    '--clinic-primary': brand,
    '--clinic-accent': accent,
    '--clinic-accent-strong': accentStrong,
    '--clinic-brand-strong': strong,
    '--clinic-brand-action': action,
    '--clinic-brand-hover': hover,
    '--clinic-brand-edge': edge,
    '--clinic-brand-veil': veil,
    '--clinic-brand-soft': soft,
    '--clinic-brand-tint': tint,
    '--clinic-on-brand': readableForeground(action),
    '--clinic-on-brand-strong': readableForeground(strong),
    '--clinic-on-brand-soft': contrastSafeColor(brand, soft, ACTION_CONTRAST),
    '--clinic-ink': CLINIC_INK,
    '--clinic-paper': CLINIC_PAPER,
    '--clinic-line': line,
    '--clinic-focus': focus,
    // 병원 표면에 남아 있는 legacy Revisit 팔레트 슬롯을 같은 ramp로 잇는다.
    '--clinic-revisit-primary-95': tint,
    '--clinic-revisit-primary-90': soft,
    '--clinic-revisit-primary-70': veil,
    '--clinic-revisit-primary-50': edge,
    '--clinic-revisit-primary-40': action,
    '--clinic-revisit-primary-30': hover,
    '--clinic-revisit-primary-20': strong,
    '--clinic-revisit-primary-10': strong,
  }
}

function isColorectalClinic(specialties: string[]): boolean {
  const joined = specialties.join(' ').replaceAll(' ', '').toLowerCase()
  return ['대장항문', '항문외과', 'colorectal', 'proctology'].some((keyword) =>
    joined.includes(keyword),
  )
}

export function selectClinicHeroImage(
  hospital: Pick<Hospital, 'hero_image_url' | 'hero_media_kind' | 'photos' | 'specialties'>,
): string | null {
  const hero = hospital.hero_media_kind === 'VERIFIED_FACILITY' || hospital.hero_media_kind === 'BRAND_GRAPHIC'
    ? resolveAssetUrl(hospital.hero_image_url)
    : null
  if (hero) return hero

  const candidates = hospital.photos.filter((photo) =>
    ['PHOTO_CLINIC_EXTERIOR', 'PHOTO_CLINIC_INTERIOR', 'PHOTO_TREATMENT_ROOM'].includes(photo.source_type) &&
    photo.asset_kind !== 'EDITORIAL_GRAPHIC' &&
    (!photo.approved_usage || photo.approved_usage.includes('HERO')),
  )
  // 같은 선호 단계에서는 API가 준 순서를 지킨다 — 운영자가 정한 순서를 뒤집지 않는다.
  const clinicPhoto = candidates.reduce<Hospital['photos'][number] | null>(
    (best, photo) =>
      best === null || heroCandidateRank(photo) < heroCandidateRank(best) ? photo : best,
    null,
  )
  return resolveAssetUrl(clinicPhoto?.url ?? null)
}

/** 대기실·접수·로비처럼 "도착해서 처음 보는 곳"을 가리키는 내부 사진 제목. */
const ARRIVAL_INTERIOR_TITLE = /대기실|접수|로비|현관|입구|안내/

/**
 * 히어로로 쓸 사진의 선호 순서 (작을수록 먼저).
 *
 * 첫 화면은 "여기가 맞는 병원인가"를 확인하는 자리다. 간판이 보이는 외관이 가장 강한
 * 근거이고, 없으면 도착 동선(대기실·접수)의 내부 사진, 그다음 다른 내부, 마지막이
 * 진료·시술실이다. 시술실을 첫 화면에 크게 거는 것은 환자에게 가장 불안한 장면이다.
 */
function heroCandidateRank(photo: Hospital['photos'][number]): number {
  if (photo.source_type === 'PHOTO_CLINIC_EXTERIOR') return 0
  if (photo.source_type === 'PHOTO_CLINIC_INTERIOR') {
    return ARRIVAL_INTERIOR_TITLE.test(photo.title || '') ? 1 : 2
  }
  return 3
}

function isVerifiedDoctorAsset(photo: Hospital['photos'][number]): boolean {
  return (
    photo.source_type === 'PHOTO_DOCTOR' &&
    photo.asset_kind === 'VERIFIED_REAL_PERSON' &&
    Boolean(photo.approved_usage?.includes('DOCTOR_IDENTITY'))
  )
}

export function selectVerifiedDoctorImage(photos: Hospital['photos']): string | null {
  const doctorPhoto = photos.find(isVerifiedDoctorAsset)
  return resolveAssetUrl(doctorPhoto?.url ?? null)
}

type RepresentativeCandidate = Pick<HospitalPhysician, 'is_representative' | 'title' | 'display_order'>

const REPRESENTATIVE_TITLE = /대표\s*원장/
const ASSOCIATE_TITLE = /부\s*원장/
const DIRECTOR_TITLE = /원장/

function representativeRank(physician: RepresentativeCandidate): number {
  if (physician.is_representative) return 0
  const title = physician.title ?? ''
  if (REPRESENTATIVE_TITLE.test(title)) return 1
  if (DIRECTOR_TITLE.test(title) && !ASSOCIATE_TITLE.test(title)) return 2
  return 3
}

/**
 * 병원을 대표하는 의료진 한 명 — 대표 표시 > 직함(대표원장, 그다음 부원장이 아닌 원장) >
 * 표시 순번. 같은 순위에서만 순번을 보므로 "목록의 첫 행"이 대표를 정하지 않는다.
 */
export function selectRepresentativePhysician<T extends RepresentativeCandidate>(
  physicians: readonly T[],
): T | null {
  let best: T | null = null
  for (const physician of physicians) {
    if (
      best === null ||
      representativeRank(physician) < representativeRank(best) ||
      (representativeRank(physician) === representativeRank(best) &&
        (physician.display_order ?? 0) < (best.display_order ?? 0))
    ) {
      best = physician
    }
  }
  return best
}

/**
 * 어느 의료진에게도 연결되지 않은 검증된 원장 사진. 다른 의료진에게 연결된 사진은 그
 * 사람의 얼굴이므로 대표 자리에 쓰지 않는다 — 사진 목록은 최근 갱신 순이라 나중에 올린
 * 부원장 사진이 맨 앞에 온다.
 */
export function selectUnlinkedVerifiedDoctorImage(
  photos: Hospital['photos'],
  physicians: readonly Pick<HospitalPhysician, 'photo_url'>[],
): string | null {
  const linked = new Set(
    physicians.map((physician) => physician.photo_url).filter((url): url is string => Boolean(url)),
  )
  for (const photo of photos) {
    if (!isVerifiedDoctorAsset(photo)) continue
    const url = resolveAssetUrl(photo.url)
    if (url && !linked.has(url)) return url
  }
  return null
}

/**
 * 병원 대표 이미지(OG·구조화 데이터)에 쓰는 대표원장 사진. 검증된 원장 사진만 쓰고, 고르는
 * 순서는 대표 의료진에게 연결된 사진 > 백엔드가 고른 `director_photo_url` > 누구에게도
 * 연결되지 않은 사진이다. 진료과·slug로 사진을 추정하지 않는다.
 */
export function selectClinicDirectorImage(
  hospital: Pick<Hospital, 'slug' | 'director_photo_url' | 'photos'> & {
    physicians?: HospitalPhysician[] | null
  },
): string | null {
  const verified = new Set(
    hospital.photos
      .filter(isVerifiedDoctorAsset)
      .map((photo) => resolveAssetUrl(photo.url))
      .filter((url): url is string => Boolean(url)),
  )
  if (verified.size === 0) return null

  const physicians = (hospital.physicians ?? []).filter((physician) => physician.name?.trim())
  const representative = selectRepresentativePhysician(physicians)
  if (representative?.photo_url && verified.has(representative.photo_url)) {
    return representative.photo_url
  }

  const others = physicians.filter((physician) => physician !== representative)
  const otherPhotos = new Set(others.map((physician) => physician.photo_url).filter(Boolean))
  const director = resolveAssetUrl(hospital.director_photo_url)
  if (director && verified.has(director) && !otherPhotos.has(director)) return director

  // 대표가 여럿이면 연결 없는 사진이 누구의 얼굴인지 단정할 수 없다.
  if (physicians.filter((physician) => physician.is_representative).length > 1) return null
  return selectUnlinkedVerifiedDoctorImage(hospital.photos, others)
}

export function absoluteClinicImageUrl(imageUrl: string | null, base: string): string | null {
  if (!imageUrl) return null
  if (imageUrl.startsWith('http://') || imageUrl.startsWith('https://')) return imageUrl
  try {
    return new URL(imageUrl, `${base.replace(/\/$/, '')}/`).toString()
  } catch {
    return null
  }
}

export function clinicEditorialFallbacks(specialties: string[]): string[] {
  if (isColorectalClinic(specialties)) {
    return [
      '/clinic/specialties/colorectal/fiber-meal.png',
      '/clinic/specialties/colorectal/symptom-guide.png',
      '/clinic/specialties/colorectal/routine-clock.png',
    ]
  }
  return ['/landing/reputation-clinic-trust-interior.png']
}
