import assert from 'node:assert/strict'
import test from 'node:test'

import {
  absoluteClinicImageUrl,
  buildClinicThemeStyle,
  clinicEditorialFallbacks,
  selectClinicDirectorImage,
  selectClinicHeroImage,
} from './clinic-theme.ts'

test('clinic theme accepts valid hospital colors and rejects malformed values', () => {
  assert.deepEqual(
    buildClinicThemeStyle({
      brand_primary_color: '#17365d',
      brand_accent_color: '#b79045',
    }),
    {
      '--clinic-primary': '#17365D',
      '--clinic-accent': '#B79045',
    },
  )

  assert.deepEqual(
    buildClinicThemeStyle({
      brand_primary_color: 'navy',
      brand_accent_color: 'gold',
    }),
    {
      '--clinic-primary': '#17365D',
      '--clinic-accent': '#B79045',
    },
  )
})

test('colorectal clinics receive the generated specialty visual set as a safe fallback', () => {
  assert.equal(
    selectClinicHeroImage({
      hero_image_url: null,
      photos: [],
      specialties: ['외과', '대장항문외과'],
    }),
    '/clinic/specialties/colorectal/hero-consultation.png',
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
      hero_image_url: 'https://cdn.example.com/hero.jpg',
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
    'https://cdn.example.com/hero.jpg',
  )
})

test('curated clinic director photo replaces a legacy illustration for the configured hospital', () => {
  assert.equal(
    selectClinicDirectorImage({
      slug: 'jangpyeonhanoegwayiweon',
      director_photo_url: '/api/v1/public/hospitals/demo/assets/legacy-illustration',
      photos: [],
    }),
    '/clinic/specialties/colorectal/director-lee-seong-geun.png',
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
        },
      ],
    }),
    'http://localhost:8000/api/v1/public/jangpyeonhanoegwayiweon/assets/doctor-real',
  )
})
