import type { CSSProperties } from 'react'

import { resolveAssetUrl, type Hospital } from './hospital-payload.ts'

export const DEFAULT_CLINIC_PRIMARY = '#17365D'
export const DEFAULT_CLINIC_ACCENT = '#B79045'

const HEX_COLOR = /^#[0-9a-f]{6}$/i

const CURATED_DIRECTOR_IMAGES: Record<string, string> = {
  jangpyeonhanoegwayiweon:
    '/clinic/specialties/colorectal/director-lee-seong-geun.png',
}

type ThemeStyle = CSSProperties & Record<`--clinic-${string}`, string>

export function normalizeClinicColor(value: string | null | undefined, fallback: string): string {
  const color = (value || '').trim()
  return HEX_COLOR.test(color) ? color.toUpperCase() : fallback
}

export function buildClinicThemeStyle(
  hospital: Pick<Hospital, 'brand_primary_color' | 'brand_accent_color'>,
): ThemeStyle {
  return {
    '--clinic-primary': normalizeClinicColor(
      hospital.brand_primary_color,
      DEFAULT_CLINIC_PRIMARY,
    ),
    '--clinic-accent': normalizeClinicColor(
      hospital.brand_accent_color,
      DEFAULT_CLINIC_ACCENT,
    ),
  }
}

function isColorectalClinic(specialties: string[]): boolean {
  const joined = specialties.join(' ').replaceAll(' ', '').toLowerCase()
  return ['대장항문', '항문외과', 'colorectal', 'proctology'].some((keyword) =>
    joined.includes(keyword),
  )
}

export function selectClinicHeroImage(
  hospital: Pick<Hospital, 'hero_image_url' | 'photos' | 'specialties'>,
): string {
  if (hospital.hero_image_url) return hospital.hero_image_url

  const clinicPhoto = hospital.photos.find((photo) =>
    ['PHOTO_CLINIC_INTERIOR', 'PHOTO_TREATMENT_ROOM'].includes(photo.source_type),
  )
  if (clinicPhoto?.url) return clinicPhoto.url

  if (isColorectalClinic(hospital.specialties)) {
    return '/clinic/specialties/colorectal/hero-consultation.png'
  }

  return '/landing/reputation-clinic-trust-interior.png'
}

/**
 * 운영팀이 사용 승인을 확인한 병원별 실사진을 우선 사용한다.
 * 일반 병원은 프로필에 등록된 공개 자산을 그대로 사용하므로 공통 레이아웃은 유지된다.
 */
export function selectClinicDirectorImage(
  hospital: Pick<Hospital, 'slug' | 'director_photo_url' | 'photos'>,
): string | null {
  const doctorPhoto = hospital.photos.find((photo) => photo.source_type === 'PHOTO_DOCTOR')
  const approvedAsset = resolveAssetUrl(doctorPhoto?.url ?? null)
  if (approvedAsset) return approvedAsset

  const curated = CURATED_DIRECTOR_IMAGES[hospital.slug]
  if (curated) return curated

  const direct = resolveAssetUrl(hospital.director_photo_url)
  if (direct) return direct
  return null
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
