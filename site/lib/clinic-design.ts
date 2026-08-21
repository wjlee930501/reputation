import type { HospitalPhoto } from './hospital-payload.ts'

export type ClinicContentDensity = 'sparse' | 'standard' | 'rich'
export type ClinicAccessMode = 'urgent' | 'appointment' | 'specialist'
export type ClinicMediaMode = 'verified-real' | 'brand-graphic' | 'typographic'

export interface ClinicNavigationItem {
  readonly href: string
  readonly label: string
  readonly ariaCurrent: 'page' | undefined
}

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

function routePath(route: string): string {
  try {
    const pathname = new URL(route, 'https://clinic.local').pathname
    return pathname === '/' ? pathname : pathname.replace(/\/+$/, '')
  } catch {
    return route
  }
}

function isCurrentClinicRoute(pathname: string, href: string): boolean {
  const currentPath = routePath(pathname)
  const targetPath = routePath(href)
  return (
    currentPath === targetPath ||
    currentPath.startsWith(`${targetPath}/`) ||
    currentPath.endsWith(targetPath) ||
    currentPath.includes(`${targetPath}/`)
  )
}

export function getClinicNavigation(hospitalRootUrl: string, pathname: string): readonly ClinicNavigationItem[] {
  const items = [
    { href: `${hospitalRootUrl}/treatments`, label: '진료 영역' },
    { href: `${hospitalRootUrl}/visit`, label: '진료시간·오시는 길' },
    { href: `${hospitalRootUrl}/doctor`, label: '의료진' },
    { href: `${hospitalRootUrl}/contents`, label: '건강 정보' },
  ] as const

  return items.map((item) => ({
    ...item,
    ariaCurrent: isCurrentClinicRoute(pathname, item.href) ? 'page' : undefined,
  }))
}

export function selectClinicGalleryPhotos(
  photos: HospitalPhoto[],
  requestedLimit = 6,
): { photos: HospitalPhoto[]; total: number; remaining: number } {
  const facilityPhotos = photos.filter((photo) => (
    FACILITY_PHOTO_TYPES.has(photo.source_type) &&
    photo.asset_kind === 'VERIFIED_FACILITY' &&
    Boolean(photo.approved_usage?.includes('GALLERY'))
  ))
  const limit = Math.max(1, Math.min(8, requestedLimit))
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
