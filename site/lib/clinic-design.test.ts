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

test('the gallery reads outside-in: exterior, then interior, then treatment rooms', () => {
  // API 순서만 따르면 첫 타일이 시술실이 되는 병원이 생긴다 — 환자가 "여기가 맞나"를
  // 확인하는 화면인데 가장 불안한 장면부터 보게 된다.
  const photos: HospitalPhoto[] = [
    { id: 'room-1', source_type: 'PHOTO_TREATMENT_ROOM', title: '시술실 A', url: '/room-1.jpg' },
    { id: 'in-1', source_type: 'PHOTO_CLINIC_INTERIOR', title: '대기실', url: '/in-1.jpg' },
    { id: 'ex-1', source_type: 'PHOTO_CLINIC_EXTERIOR', title: '건물 외관', url: '/ex-1.jpg' },
    { id: 'in-2', source_type: 'PHOTO_CLINIC_INTERIOR', title: '진료실 복도', url: '/in-2.jpg' },
    { id: 'ex-2', source_type: 'PHOTO_CLINIC_EXTERIOR', title: '간판', url: '/ex-2.jpg' },
  ]

  assert.deepEqual(
    selectClinicGalleryPhotos(photos).photos.map((photo) => photo.id),
    ['ex-1', 'ex-2', 'in-1', 'in-2', 'room-1'],
  )
})

test('the photo already used as the hero is not repeated in the gallery', () => {
  const photos: HospitalPhoto[] = [
    { id: 'hero', source_type: 'PHOTO_CLINIC_EXTERIOR', title: '외관', url: '/hero.jpg' },
    { id: 'other', source_type: 'PHOTO_CLINIC_INTERIOR', title: '대기실', url: '/other.jpg' },
  ]
  const selection = selectClinicGalleryPhotos(photos, 6, { excludeUrl: '/hero.jpg' })
  assert.deepEqual(selection.photos.map((photo) => photo.id), ['other'])
  // 전체 수도 함께 줄어야 "전체 N장 보기"가 실제로 더 보여줄 수 있는 수를 말한다.
  assert.equal(selection.total, 1)
  assert.equal(selection.remaining, 0)
})

test('doctor role does not repeat the representative-director label', () => {
  assert.equal(selectDoctorRole([]), null)
  assert.equal(selectDoctorRole(['대표원장', '내과 전문의']), '내과 전문의')
  assert.equal(selectDoctorRole(['피부과 전문의']), '피부과 전문의')
})

test('the home gallery links to the full library instead of only naming a number', () => {
  // "전체 22장 중 6장" 문장만 두면 나머지 16장을 볼 방법이 화면에 없다.
  const component = readFileSync(
    join(HERE, '..', 'app', '[slug]', '_components', 'ClinicGallery.tsx'),
    'utf8',
  )
  assert.match(component, /공간 사진 전체 \{countLabel\(selection\.total, '장'\)\} 보기/)
  assert.match(component, /className="clinic-tx-directory-more"/)

  const home = readFileSync(join(HERE, '..', 'app', '[slug]', 'page.tsx'), 'utf8')
  assert.match(home, /allPhotosHref=\{`\$\{hospitalRootUrl\}\/visit#gallery`\}/)
  // 히어로가 쓰는 사진은 갤러리에서 뺀다 — 첫 화면과 같은 사진이 바로 아래 또 나온다.
  assert.match(home, /excludeUrl=\{heroPhotoUrl\}/)
})

test('the visit page owns the anchor the home gallery links to', () => {
  const component = readFileSync(
    join(HERE, '..', 'app', '[slug]', '_components', 'VisitGallery.tsx'),
    'utf8',
  )
  assert.match(component, /id="gallery"/)
  // 분류별 소제목이 있어야 "주차장에서 어디로 들어가는가"를 사진 순서로 읽을 수 있다.
  assert.match(component, /CLINIC_GALLERY_CATEGORY_ORDER\.map/)
  assert.match(component, /clinic-visit-gallery-heading/)
  // /visit은 미리보기가 아니다 — 상한으로 잘라내지 않는다.
  assert.doesNotMatch(component, /previewLimit/)

  const visit = readFileSync(join(HERE, '..', 'app', '[slug]', 'visit', 'page.tsx'), 'utf8')
  assert.match(visit, /<VisitGallery photos=\{facilityPhotos\} policy=\{clinicGalleryPolicy\('visit'\)\} \/>/)
})
