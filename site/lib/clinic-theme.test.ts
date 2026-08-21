import assert from 'node:assert/strict'
import test from 'node:test'

import {
  absoluteClinicImageUrl,
  buildClinicThemeStyle,
  clinicEditorialFallbacks,
  contrastRatio,
  selectClinicDirectorImage,
  selectClinicHeroImage,
} from './clinic-theme.ts'

test('clinic theme accepts valid hospital colors and rejects malformed values', () => {
  const branded = buildClinicThemeStyle({
    brand_primary_color: '#17365d',
    brand_accent_color: '#b79045',
  })
  assert.equal(branded['--clinic-brand'], '#17365D')
  assert.equal(branded['--clinic-accent'], '#B79045')
  assert.equal(branded['--clinic-primary'], '#17365D')
  assert.ok(contrastRatio(branded['--clinic-brand-action'], branded['--clinic-on-brand']) >= 4.5)

  const fallback = buildClinicThemeStyle({
    brand_primary_color: 'navy',
    brand_accent_color: 'gold',
  })
  assert.equal(fallback['--clinic-brand'], '#17365D')
  assert.equal(fallback['--clinic-accent'], '#B79045')
})

test('light clinic colors receive a readable action foreground without erasing the brand', () => {
  // Given a light gold hospital identity
  const theme = buildClinicThemeStyle({
    brand_primary_color: '#D6A72C',
    brand_accent_color: '#46766A',
  })

  // Then the identity remains visible while action text passes WCAG AA.
  assert.equal(theme['--clinic-brand'], '#D6A72C')
  assert.notEqual(theme['--clinic-brand-soft'], theme['--clinic-brand'])
  assert.ok(contrastRatio(theme['--clinic-brand-action'], theme['--clinic-paper']) >= 4.5)
  assert.ok(contrastRatio(theme['--clinic-brand-action'], theme['--clinic-on-brand']) >= 4.5)
  assert.ok(contrastRatio(theme['--clinic-focus'], theme['--clinic-paper']) >= 3)
})

test('hero imagery never invents a clinic photograph when no verified facility asset exists', () => {
  assert.equal(
    selectClinicHeroImage({
      hero_image_url: null,
      hero_media_kind: null,
      photos: [],
      specialties: ['외과', '대장항문외과'],
    }),
    null,
  )

  assert.deepEqual(clinicEditorialFallbacks(['대장항문외과']), [
    '/clinic/specialties/colorectal/fiber-meal.png',
    '/clinic/specialties/colorectal/symptom-guide.png',
    '/clinic/specialties/colorectal/routine-clock.png',
  ])
})

test('approved profile hero and clinic photos take precedence over generic imagery', () => {
  assert.equal(
    selectClinicHeroImage({
      hero_image_url: 'http://localhost:8000/hero.jpg',
      hero_media_kind: 'VERIFIED_FACILITY',
      photos: [
        {
          id: 'interior',
          source_type: 'PHOTO_CLINIC_INTERIOR',
          title: '진료실',
          url: 'https://cdn.example.com/interior.jpg',
        },
      ],
      specialties: ['대장항문외과'],
    }),
    'http://localhost:8000/hero.jpg',
  )
})

test('approved brand graphic can lead the hero without being represented as a clinic photo', () => {
  assert.equal(
    selectClinicHeroImage({
      hero_image_url: 'http://localhost:8000/clinic-identity.png',
      hero_media_kind: 'BRAND_GRAPHIC',
      photos: [],
      specialties: ['내과'],
    }),
    'http://localhost:8000/clinic-identity.png',
  )
})

test('director identity never falls back to slug-specific artwork', () => {
  assert.equal(
    selectClinicDirectorImage({
      slug: 'jangpyeonhanoegwayiweon',
      director_photo_url: null,
      photos: [],
    }),
    null,
  )

  assert.equal(
    absoluteClinicImageUrl(
      '/clinic/specialties/colorectal/director-lee-seong-geun.png',
      'https://jangclinic.kr',
    ),
    'https://jangclinic.kr/clinic/specialties/colorectal/director-lee-seong-geun.png',
  )
})

test('approved real doctor photo takes precedence over clinic fallback artwork', () => {
  assert.equal(
    selectClinicDirectorImage({
      slug: 'jangpyeonhanoegwayiweon',
      director_photo_url: null,
      photos: [
        {
          id: 'doctor-real',
          source_type: 'PHOTO_DOCTOR',
          title: '이성근 원장 진료 사진',
          url: '/api/v1/public/jangpyeonhanoegwayiweon/assets/doctor-real',
          asset_kind: 'VERIFIED_REAL_PERSON',
          approved_usage: ['DOCTOR_IDENTITY'],
        },
      ],
    }),
    'http://localhost:8000/api/v1/public/jangpyeonhanoegwayiweon/assets/doctor-real',
  )
})

test('editorial character art cannot enter a named doctor identity slot', () => {
  assert.equal(
    selectClinicDirectorImage({
      slug: 'clinic',
      director_photo_url: '/api/v1/public/clinic/assets/character',
      photos: [
        {
          id: 'character',
          source_type: 'PHOTO_DOCTOR',
          title: '원장 캐릭터 일러스트',
          url: '/api/v1/public/clinic/assets/character',
          asset_kind: 'EDITORIAL_GRAPHIC',
          approved_usage: ['CONTENT_EDITORIAL'],
        },
      ],
    }),
    null,
  )
})
