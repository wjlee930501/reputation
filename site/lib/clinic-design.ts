import type { HospitalPhoto } from './hospital-payload.ts'

export type ClinicContentDensity = 'sparse' | 'standard' | 'rich'
export type ClinicAccessMode = 'urgent' | 'appointment' | 'specialist'
export type ClinicMediaMode = 'verified-real' | 'brand-graphic' | 'typographic'

export interface ClinicComposition {
  featuredSecondaryLimit: number
  galleryPreviewLimit: number
  showAnswerClusters: boolean
  showCareFlow: boolean
}

const FACILITY_PHOTO_TYPES = new Set<HospitalPhoto['source_type']>([
  'PHOTO_CLINIC_EXTERIOR',
  'PHOTO_CLINIC_INTERIOR',
  'PHOTO_TREATMENT_ROOM',
])

export function clinicContentDensity(contentCount: number): ClinicContentDensity {
  if (contentCount <= 2) return 'sparse'
  if (contentCount <= 8) return 'standard'
  return 'rich'
}

export function clinicComposition(density: ClinicContentDensity): ClinicComposition {
  if (density === 'sparse') {
    return {
      featuredSecondaryLimit: 0,
      galleryPreviewLimit: 4,
      showAnswerClusters: false,
      showCareFlow: false,
    }
  }
  if (density === 'standard') {
    return {
      featuredSecondaryLimit: 2,
      galleryPreviewLimit: 6,
      showAnswerClusters: true,
      showCareFlow: true,
    }
  }
  return {
    featuredSecondaryLimit: 4,
    galleryPreviewLimit: 8,
    showAnswerClusters: true,
    showCareFlow: true,
  }
}

/** 갤러리를 렌더하는 표면. 표면마다 사진을 몇 장부터·몇 장까지 보여줄지 다르다. */
export type ClinicGallerySurface = 'home' | 'visit'

export interface ClinicGalleryPolicy {
  /** 이 장수 미만이면 갤러리를 아예 그리지 않는다. */
  minimumPhotoCount: number
  /** 한 번에 보여줄 상한. */
  previewLimit: number
}

/** selectClinicGalleryPhotos가 허용하는 상한 — 정책과 선택 로직이 같은 값을 본다. */
export const CLINIC_GALLERY_MAX = 8

/**
 * 홈과 `/visit`의 갤러리 장수 정책 (P-E-2).
 *
 * 두 화면이 같은 컴포넌트를 쓰면서 홈은 기본값(3장 게이트 + 밀도별 상한), `/visit`은
 * 인자 하나만 넘겨(1장 게이트 + 컴포넌트 기본 상한 6) 서로 다른 규칙으로 굴러갔다.
 * 컴포넌트 기본값이 정책을 절반씩 소유하면 한쪽만 바꿔도 조용히 갈리므로,
 * 두 표면의 값을 여기서 함께 정한다.
 *
 * 홈은 사진이 3장 이상 모여 "둘러보기"가 될 때만 노출하고 상한은 콘텐츠 밀도를 따른다.
 * `/visit`은 방문 준비가 목적이라 1장부터 보여주고 승인된 사진을 상한까지 다 보여준다.
 */
export function clinicGalleryPolicy(
  surface: ClinicGallerySurface,
  density: ClinicContentDensity = 'standard',
): ClinicGalleryPolicy {
  if (surface === 'visit') {
    return { minimumPhotoCount: 1, previewLimit: CLINIC_GALLERY_MAX }
  }
  return { minimumPhotoCount: 3, previewLimit: clinicComposition(density).galleryPreviewLimit }
}

export function resolveClinicAccessMode(input: {
  configuredMode?: string | null
  specialties: string[]
  businessHours: Record<string, string> | null | undefined
  boardCertifications: string[]
}): ClinicAccessMode {
  if (input.configuredMode === 'urgent') return 'urgent'
  if (input.configuredMode === 'appointment') return 'appointment'
  if (input.configuredMode === 'specialist') return 'specialist'
  const specialtyText = input.specialties.join(' ').replaceAll(' ', '')
  const hoursText = Object.values(input.businessHours ?? {}).join(' ')
  if (specialtyText.includes('응급') || /(?:20|21|22|23):\d{2}/.test(hoursText)) return 'urgent'
  if (input.boardCertifications.some((certification) => certification.trim())) return 'specialist'
  return 'appointment'
}

export function resolveClinicMediaMode(input: {
  hasVerifiedFacilityPhoto: boolean
  hasBrandGraphic?: boolean
  hasLogo: boolean
}): ClinicMediaMode {
  if (input.hasVerifiedFacilityPhoto) return 'verified-real'
  if (input.hasBrandGraphic || input.hasLogo) return 'brand-graphic'
  return 'typographic'
}

export function displayClinicLabels(values: string[], limit = 2): string[] {
  const normalized = values.map((value) => value.trim()).filter(Boolean)
  return [...new Set(normalized)].slice(0, Math.max(0, limit))
}

export function selectClinicGalleryPhotos(
  photos: HospitalPhoto[],
  requestedLimit = 6,
): { photos: HospitalPhoto[]; total: number; remaining: number } {
  const facilityPhotos = photos.filter((photo) => (
    FACILITY_PHOTO_TYPES.has(photo.source_type) &&
    photo.asset_kind !== 'EDITORIAL_GRAPHIC' &&
    (!photo.approved_usage || photo.approved_usage.includes('GALLERY'))
  ))
  const limit = Math.max(1, Math.min(CLINIC_GALLERY_MAX, requestedLimit))
  const selected = facilityPhotos.slice(0, limit)
  return {
    photos: selected,
    total: facilityPhotos.length,
    remaining: Math.max(0, facilityPhotos.length - selected.length),
  }
}

export function selectDoctorRole(boardCertifications: string[]): string | null {
  return boardCertifications
    .map((certification) => certification.trim())
    .find((certification) => certification && certification !== '대표원장') ?? null
}
