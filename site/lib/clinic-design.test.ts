import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import type { HospitalPhoto } from './hospital-payload.ts'

import {
  CLINIC_GALLERY_MAX,
  clinicContentDensity,
  clinicComposition,
  clinicGalleryPolicy,
  displayClinicLabels,
  resolveClinicAccessMode,
  resolveClinicMediaMode,
  selectClinicGalleryPhotos,
  selectDoctorRole,
} from './clinic-design.ts'

const HERE = dirname(fileURLToPath(import.meta.url))

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

test('gallery selection exposes a representative set instead of an unbounded photo wall', () => {
  const photos = Array.from({ length: 12 }, (_, index) => ({
    id: String(index),
    source_type: index === 0 ? 'PHOTO_DOCTOR' as const : 'PHOTO_CLINIC_INTERIOR' as const,
    title: `공간 ${index}`,
    url: `/photo-${index}.jpg`,
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

test('the home and visit gallery counts come from one policy, not component defaults', () => {
  // P-E-2 — 홈은 컴포넌트 기본값(3장 게이트·6장 상한), /visit은 인자 하나(1장 게이트)만
  // 넘겨서 두 화면이 규칙을 반씩 나눠 갖고 있었다. 정책은 한 함수가 소유한다.
  assert.deepEqual(clinicGalleryPolicy('home', 'sparse'), {
    minimumPhotoCount: 3,
    previewLimit: 4,
  })
  assert.deepEqual(clinicGalleryPolicy('home', 'rich'), {
    minimumPhotoCount: 3,
    previewLimit: CLINIC_GALLERY_MAX,
  })
  assert.deepEqual(clinicGalleryPolicy('visit'), {
    minimumPhotoCount: 1,
    previewLimit: CLINIC_GALLERY_MAX,
  })

  // 상한은 선택 로직이 실제로 허용하는 값과 같아야 한다.
  const photos = Array.from({ length: 20 }, (_, index) => ({
    id: String(index),
    source_type: 'PHOTO_CLINIC_INTERIOR' as const,
    title: `공간 ${index}`,
    url: `/photo-${index}.jpg`,
  }))
  assert.equal(
    selectClinicGalleryPhotos(photos, clinicGalleryPolicy('visit').previewLimit).photos.length,
    CLINIC_GALLERY_MAX,
  )
})

test('both gallery surfaces pass an explicit policy so the component keeps no default', () => {
  const component = readFileSync(
    join(HERE, '..', 'app', '[slug]', '_components', 'ClinicGallery.tsx'),
    'utf8',
  )
  assert.doesNotMatch(component, /minimumPhotoCount\s*=/)
  assert.doesNotMatch(component, /previewLimit\s*=/)

  const home = readFileSync(join(HERE, '..', 'app', '[slug]', 'page.tsx'), 'utf8')
  const visit = readFileSync(join(HERE, '..', 'app', '[slug]', 'visit', 'page.tsx'), 'utf8')
  assert.match(home, /policy=\{clinicGalleryPolicy\('home', contentDensity\)\}/)
  assert.match(visit, /policy=\{clinicGalleryPolicy\('visit'\)\}/)
})

test('doctor role does not repeat the representative-director label', () => {
  assert.equal(selectDoctorRole([]), null)
  assert.equal(selectDoctorRole(['대표원장', '내과 전문의']), '내과 전문의')
  assert.equal(selectDoctorRole(['피부과 전문의']), '피부과 전문의')
})
