import assert from 'node:assert/strict'
import test from 'node:test'

import type { HospitalPhoto } from './hospital-payload.ts'

import {
  clinicContentDensity,
  clinicComposition,
  displayClinicLabels,
  getClinicNavigation,
  resolveClinicAccessMode,
  resolveClinicMediaMode,
  selectClinicGalleryPhotos,
  selectDoctorRole,
} from './clinic-design.ts'

test('content density follows the shared sparse, standard, and rich composition thresholds', () => {
  // Given hospitals with materially different amounts of published information
  // When the composition tier is selected
  // Then sparse pages avoid repeating a single item while rich pages can expose discovery modules.
  assert.equal(clinicContentDensity(0), 'sparse')
  assert.equal(clinicContentDensity(2), 'sparse')
  assert.equal(clinicContentDensity(3), 'standard')
  assert.equal(clinicContentDensity(8), 'standard')
  assert.equal(clinicContentDensity(9), 'rich')
})

test('each content density receives a materially different page composition', () => {
  assert.deepEqual(clinicComposition('sparse'), {
    featuredSecondaryLimit: 0,
    galleryPreviewLimit: 4,
    showAnswerClusters: false,
    showCareFlow: false,
  })
  assert.equal(clinicComposition('standard').featuredSecondaryLimit, 2)
  assert.equal(clinicComposition('rich').galleryPreviewLimit, 8)
})

test('access and media modes are derived from approved profile facts without slug exceptions', () => {
  assert.equal(resolveClinicAccessMode({
    specialties: ['응급의학과'],
    businessHours: { mon: '09:00-18:00' },
    boardCertifications: [],
  }), 'urgent')
  assert.equal(resolveClinicAccessMode({
    configuredMode: 'specialist',
    specialties: [],
    businessHours: null,
    boardCertifications: [],
  }), 'specialist')
  assert.equal(resolveClinicMediaMode({ hasVerifiedFacilityPhoto: true, hasLogo: false }), 'verified-real')
  assert.equal(resolveClinicMediaMode({ hasVerifiedFacilityPhoto: false, hasLogo: true }), 'brand-graphic')
})

test('hero and header labels stay concise even when onboarding contains many specialties', () => {
  assert.deepEqual(
    displayClinicLabels([' 내과 ', '', '가정의학과', '건강검진', '내과'], 2),
    ['내과', '가정의학과'],
  )
})

test('shared navigation marks only the containing route as current', () => {
  // Given a nested treatment page in a hospital's public hub
  const navigation = getClinicNavigation('/hanul-clinic', '/hanul-clinic/treatments/hemorrhoids')

  // When the shared header derives its active item
  // Then only the treatment section is exposed as the current page.
  assert.deepEqual(
    navigation.map(({ label, ariaCurrent }) => ({ label, ariaCurrent })),
    [
      { label: '진료 영역', ariaCurrent: 'page' },
      { label: '진료시간·오시는 길', ariaCurrent: undefined },
      { label: '의료진', ariaCurrent: undefined },
      { label: '건강 정보', ariaCurrent: undefined },
    ],
  )

  // Given the same visit route on a connected custom domain
  const customDomainNavigation = getClinicNavigation('https://hanul.example', '/visit')

  // When the browser reports its host-relative pathname
  // Then the same visible navigation item remains current.
  assert.equal(customDomainNavigation[1]?.ariaCurrent, 'page')

  // Given the same custom-domain clinic rendered through the platform preview path
  const previewNavigation = getClinicNavigation(
    'https://hanul.example',
    '/hanul-clinic/doctor/career',
  )

  // Then the route suffix still identifies the current section.
  assert.equal(previewNavigation[2]?.ariaCurrent, 'page')
})

test('gallery selection exposes a representative set instead of an unbounded photo wall', () => {
  const photos = Array.from({ length: 12 }, (_, index) => ({
    id: String(index),
    source_type: index === 0 ? 'PHOTO_DOCTOR' as const : 'PHOTO_CLINIC_INTERIOR' as const,
    title: `공간 ${index}`,
    url: `/photo-${index}.jpg`,
    asset_kind: 'VERIFIED_FACILITY' as const,
    approved_usage: ['GALLERY'],
  }))

  assert.deepEqual(selectClinicGalleryPhotos(photos), {
    photos: photos.slice(1, 7),
    total: 11,
    remaining: 5,
  })
  assert.equal(selectClinicGalleryPhotos(photos, 99).photos.length, 8)
})

test('editorial graphics never masquerade as verified clinic gallery photographs', () => {
  const photos: HospitalPhoto[] = [
    {
      id: 'verified',
      source_type: 'PHOTO_CLINIC_INTERIOR' as const,
      title: '실제 대기실',
      url: '/verified.jpg',
      asset_kind: 'VERIFIED_FACILITY',
      approved_usage: ['HERO', 'GALLERY'],
    },
    {
      id: 'graphic',
      source_type: 'PHOTO_CLINIC_INTERIOR' as const,
      title: '대기실 일러스트',
      url: '/graphic.jpg',
      asset_kind: 'EDITORIAL_GRAPHIC',
      approved_usage: ['CONTENT_EDITORIAL'],
    },
  ]

  assert.deepEqual(selectClinicGalleryPhotos(photos).photos, [photos[0]])
})

test('gallery selection rejects legacy and hero-only facility records', () => {
  // Given facility-shaped records without an explicit gallery approval
  const photos: HospitalPhoto[] = [
    {
      id: 'legacy',
      source_type: 'PHOTO_CLINIC_INTERIOR',
      title: '예전 업로드',
      url: '/legacy.jpg',
    },
    {
      id: 'hero-only',
      source_type: 'PHOTO_CLINIC_INTERIOR',
      title: '대표 이미지 전용',
      url: '/hero.jpg',
      asset_kind: 'VERIFIED_FACILITY',
      approved_usage: ['HERO'],
    },
  ]

  // When the gallery chooses public facility media
  const selection = selectClinicGalleryPhotos(photos)

  // Then neither unclassified nor wrong-usage media is presented as a gallery photo.
  assert.deepEqual(selection, { photos: [], total: 0, remaining: 0 })
})

test('doctor role does not repeat the representative-director label', () => {
  assert.equal(selectDoctorRole([]), null)
  assert.equal(selectDoctorRole(['대표원장', '내과 전문의']), '내과 전문의')
  assert.equal(selectDoctorRole(['피부과 전문의']), '피부과 전문의')
})
