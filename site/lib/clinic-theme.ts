import type { CSSProperties } from 'react'

import { resolveAssetUrl, type Hospital } from './hospital-payload.ts'

export const DEFAULT_CLINIC_PRIMARY = '#17365D'
export const DEFAULT_CLINIC_ACCENT = '#B79045'

const CLINIC_INK = '#0A1B2F'
const CLINIC_PAPER = '#FBFAF7'
const WHITE = '#FFFFFF'
const HEX_COLOR = /^#[0-9a-f]{6}$/i

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

export function buildClinicThemeStyle(
  hospital: Pick<Hospital, 'brand_primary_color' | 'brand_accent_color'>,
): ThemeStyle {
  const brand = normalizeClinicColor(hospital.brand_primary_color, DEFAULT_CLINIC_PRIMARY)
  const accent = normalizeClinicColor(hospital.brand_accent_color, DEFAULT_CLINIC_ACCENT)
  const action = contrastSafeColor(brand, CLINIC_PAPER, 4.5)
  const onBrand = readableForeground(action)
  const focus = contrastRatio(accent, CLINIC_PAPER) >= 3 ? accent : CLINIC_INK

  return {
    '--clinic-brand': brand,
    '--clinic-primary': brand,
    '--clinic-accent': accent,
    '--clinic-brand-action': action,
    '--clinic-brand-hover': mixHex(action, CLINIC_INK, 0.14),
    '--clinic-brand-soft': mixHex(brand, CLINIC_PAPER, 0.88),
    '--clinic-on-brand': onBrand,
    '--clinic-ink': CLINIC_INK,
    '--clinic-paper': CLINIC_PAPER,
    '--clinic-line': mixHex(brand, CLINIC_PAPER, 0.78),
    '--clinic-focus': focus,
    '--clinic-revisit-primary-40': action,
    '--clinic-revisit-primary-30': mixHex(action, CLINIC_INK, 0.16),
    '--clinic-revisit-primary-10': CLINIC_INK,
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

  const clinicPhoto = hospital.photos.find((photo) =>
    ['PHOTO_CLINIC_EXTERIOR', 'PHOTO_CLINIC_INTERIOR', 'PHOTO_TREATMENT_ROOM'].includes(photo.source_type) &&
    photo.asset_kind !== 'EDITORIAL_GRAPHIC' &&
    (!photo.approved_usage || photo.approved_usage.includes('HERO')),
  )
  return resolveAssetUrl(clinicPhoto?.url ?? null)
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

/** Select only an explicit doctor identity asset; never infer one from specialty or tenant slug. */
export function selectClinicDirectorImage(
  hospital: Pick<Hospital, 'slug' | 'director_photo_url' | 'photos'>,
): string | null {
  return selectVerifiedDoctorImage(hospital.photos)
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
