import type { HospitalPhoto } from './hospital-payload.ts'

const VISIT_FACILITY_PHOTO_TYPES = new Set<HospitalPhoto['source_type']>([
  'PHOTO_CLINIC_EXTERIOR',
  'PHOTO_CLINIC_INTERIOR',
  'PHOTO_TREATMENT_ROOM',
])

export function selectVisitFacilityPhotos(photos: readonly HospitalPhoto[]): HospitalPhoto[] {
  return photos.filter((photo) => VISIT_FACILITY_PHOTO_TYPES.has(photo.source_type))
}
