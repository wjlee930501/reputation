import { resolveBaseUrl } from './config.ts'
import { safeExternalHref } from './safe-url.ts'

export interface DirectorCredentials {
  medical_school?: string | null
  board_certifications?: string[] | null
  society_memberships?: string[] | null
}

export interface Hospital {
  id: string
  name: string
  slug: string
  address: string
  phone: string
  business_hours: Record<string, string>
  website_url: string | null
  blog_url: string | null
  kakao_channel_url: string | null
  google_business_profile_url: string | null
  google_maps_url: string | null
  naver_place_url: string | null
  latitude: number | null
  longitude: number | null
  wikidata_qid: string | null
  gbp_place_id: string | null
  naver_place_id: string | null
  kakao_place_id: string | null
  hira_org_id: string | null
  region: string[]
  specialties: string[]
  keywords: string[]
  director_name: string
  director_career: string
  director_philosophy: string | null
  director_photo_url: string | null
  director_credentials: DirectorCredentials | null
  treatments: Array<{ name: string; description: string }>
  aeo_domain: string | null
  photos: HospitalPhoto[]
}

export type HospitalPhotoType =
  | 'PHOTO_DOCTOR'
  | 'PHOTO_CLINIC_EXTERIOR'
  | 'PHOTO_CLINIC_INTERIOR'
  | 'PHOTO_TREATMENT_ROOM'

export interface HospitalPhoto {
  id: string
  source_type: HospitalPhotoType
  title: string
  url: string
}

type HospitalPayload = Omit<
  Hospital,
  'address' | 'phone' | 'business_hours' | 'director_name' | 'director_career'
> & {
  address: string | null
  phone: string | null
  business_hours: Record<string, string> | null
  director_name: string | null
  director_career: string | null
}

const DEV_ASSETS_BACKEND_BASE = 'http://localhost:8000'
const LOCAL_ASSET_HOSTNAMES = new Set(['localhost', '127.0.0.1', '::1'])

export function resolveAssetUrl(url: string | null | undefined): string | null {
  if (!url) return null
  const trimmed = url.trim()
  if (!trimmed) return null
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    return isAllowedAbsoluteAssetUrl(trimmed) ? trimmed : null
  }
  if (trimmed.startsWith('/')) return `${getAssetsBackendBase()}${trimmed}`
  return null
}

export function parseHospitalPayload(value: unknown): Hospital | null {
  if (!isHospitalPayload(value)) return null
  return normalizeHospitalPayload(value)
}

function getAssetsBackendBase(): string {
  return resolveBaseUrl(process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL, {
    envName: 'NEXT_PUBLIC_BACKEND_URL (or BACKEND_URL)',
    devDefault: DEV_ASSETS_BACKEND_BASE,
  })
}

function isAllowedAbsoluteAssetUrl(value: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return false
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false
  const hostname = parsed.hostname.toLowerCase()
  if (process.env.NODE_ENV !== 'production' && LOCAL_ASSET_HOSTNAMES.has(hostname)) return true
  if (isAllowedGcsAssetUrl(parsed)) return true

  const configuredBackend = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL
  if (!configuredBackend) return false
  try {
    return parsed.origin === new URL(configuredBackend).origin
  } catch {
    return false
  }
}

function isAllowedGcsAssetUrl(parsed: URL): boolean {
  if (parsed.protocol !== 'https:') return false
  const bucket = (process.env.NEXT_PUBLIC_GCP_STORAGE_BUCKET || process.env.GCP_STORAGE_BUCKET || '').trim()
  if (!bucket) return false
  const hostname = parsed.hostname.toLowerCase()
  if (hostname === 'storage.googleapis.com') {
    const firstSegment = parsed.pathname.split('/').filter(Boolean)[0] || ''
    return firstSegment === bucket
  }
  return hostname === `${bucket}.storage.googleapis.com`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

const HOSPITAL_EXTERNAL_URL_FIELDS = [
  'website_url',
  'blog_url',
  'kakao_channel_url',
  'google_business_profile_url',
  'google_maps_url',
  'naver_place_url',
] as const

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === 'number'
}

function isNullableStringRecord(value: unknown): value is Record<string, string> | null {
  if (value === null) return true
  if (!isRecord(value)) return false
  return Object.values(value).every((item) => typeof item === 'string')
}

function isDirectorCredentials(value: unknown): value is DirectorCredentials | null {
  if (value === null) return true
  if (!isRecord(value)) return false
  return (
    (value.medical_school === undefined || isNullableString(value.medical_school)) &&
    (value.board_certifications === undefined || value.board_certifications === null || isStringArray(value.board_certifications)) &&
    (value.society_memberships === undefined || value.society_memberships === null || isStringArray(value.society_memberships))
  )
}

function isHospitalPhotoPayload(value: unknown): value is HospitalPhoto {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    typeof value.source_type === 'string' &&
    typeof value.title === 'string' &&
    typeof value.url === 'string'
  )
}

function isHospitalPayload(value: unknown): value is HospitalPayload {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    typeof value.name === 'string' &&
    typeof value.slug === 'string' &&
    isNullableString(value.address) &&
    isNullableString(value.phone) &&
    isNullableStringRecord(value.business_hours) &&
    HOSPITAL_EXTERNAL_URL_FIELDS.every((field) => isNullableString(value[field])) &&
    isNullableNumber(value.latitude) &&
    isNullableNumber(value.longitude) &&
    isNullableString(value.wikidata_qid) &&
    isNullableString(value.gbp_place_id) &&
    isNullableString(value.naver_place_id) &&
    isNullableString(value.kakao_place_id) &&
    isNullableString(value.hira_org_id) &&
    isStringArray(value.region) &&
    isStringArray(value.specialties) &&
    isStringArray(value.keywords) &&
    isNullableString(value.director_name) &&
    isNullableString(value.director_career) &&
    isNullableString(value.director_philosophy) &&
    isNullableString(value.director_photo_url) &&
    isDirectorCredentials(value.director_credentials) &&
    Array.isArray(value.treatments) &&
    value.treatments.every(
      (item) => isRecord(item) && typeof item.name === 'string' && typeof item.description === 'string',
    ) &&
    isNullableString(value.aeo_domain) &&
    Array.isArray(value.photos) &&
    value.photos.every(isHospitalPhotoPayload)
  )
}

function normalizeHospitalPayload(hospital: HospitalPayload): Hospital {
  const normalized: Hospital = {
    ...hospital,
    address: hospital.address ?? '',
    phone: hospital.phone ?? '',
    business_hours: hospital.business_hours ?? {},
    director_name: hospital.director_name ?? '',
    director_career: hospital.director_career ?? '',
  }
  for (const field of HOSPITAL_EXTERNAL_URL_FIELDS) {
    normalized[field] = safeExternalHref(hospital[field])
  }
  normalized.director_photo_url = resolveAssetUrl(hospital.director_photo_url)
  normalized.photos = hospital.photos
    .map((photo) => ({ ...photo, url: resolveAssetUrl(photo.url) }))
    .filter((photo): photo is HospitalPhoto => Boolean(photo.url))
  return normalized
}
