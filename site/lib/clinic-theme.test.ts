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
})

test('the approved accent has a text-safe step so it can actually be used on screen', () => {
  // P-B-7 — 승인된 보조색이 화면에 하나도 닿지 않고 있었다. 글자에 쓰려면 종이 대비를
  // 통과해야 한다: gold #B79045는 원색 그대로는 3.2:1로 본문급에 미달한다.
  for (const accent of ['#B79045', '#D6A72C', '#FFFF00', '#46766A', '#17365D']) {
    const theme = buildClinicThemeStyle({
      brand_primary_color: '#17365D',
      brand_accent_color: accent,
    })
    assert.equal(theme['--clinic-accent'], accent.toUpperCase())
    assert.ok(
      contrastRatio(theme['--clinic-accent-strong'], theme['--clinic-paper']) >= 4.5,
      `accent ${accent}의 글자용 단계가 종이 대비를 통과하지 않는다`,
    )
  }

  // 승인된 보조색이 없으면 대표색에서 파생한다 — 두 번째 색을 지어내지 않는다.
  const derived = buildClinicThemeStyle({
    brand_primary_color: '#6F8A56',
    brand_accent_color: null,
  })
  assert.equal(derived['--clinic-accent'], '#6F8A56')
  assert.equal(derived['--clinic-accent-strong'], derived['--clinic-brand-action'])
})

test('one approved primary is enough — the whole ramp derives from it', () => {
  // AE가 로고와 대표색 하나만 승인한 병원.
  const theme = buildClinicThemeStyle({
    brand_primary_color: '#6F8A56',
    brand_accent_color: null,
  })

  // 시스템이 두 번째 브랜드 색을 지어내지 않는다.
  assert.equal(theme['--clinic-accent'], '#6F8A56')

  // 그리고 표면이 읽는 단계가 전부 채워진다.
  const rampTokens: Array<`--clinic-${string}`> = [
    '--clinic-brand-strong',
    '--clinic-brand-action',
    '--clinic-brand-hover',
    '--clinic-brand-edge',
    '--clinic-brand-veil',
    '--clinic-brand-soft',
    '--clinic-brand-tint',
    '--clinic-line',
  ]
  for (const token of rampTokens) {
    assert.match(theme[token], /^#[0-9A-F]{6}$/, `${token}이 파생되지 않았다`)
  }
})

test('the derived ramp keeps every text pairing above its contrast floor', () => {
  for (const primary of ['#17365D', '#D6A72C', '#6F8A56', '#000000', '#FFFF00', '#7C3AED']) {
    const theme = buildClinicThemeStyle({
      brand_primary_color: primary,
      brand_accent_color: null,
    })
    const label = `primary ${primary}`

    // 채워진 표면은 본문급 대비, 링크·CTA는 WCAG AA.
    assert.ok(contrastRatio(theme['--clinic-brand-strong'], theme['--clinic-paper']) >= 7, label)
    assert.ok(contrastRatio(theme['--clinic-brand-action'], theme['--clinic-paper']) >= 4.5, label)
    assert.ok(contrastRatio(theme['--clinic-brand-action'], theme['--clinic-on-brand']) >= 4.5, label)
    assert.ok(
      contrastRatio(theme['--clinic-brand-strong'], theme['--clinic-on-brand-strong']) >= 4.5,
      label,
    )
    assert.ok(contrastRatio(theme['--clinic-on-brand-soft'], theme['--clinic-brand-soft']) >= 4.5, label)
    assert.ok(contrastRatio(theme['--clinic-focus'], theme['--clinic-paper']) >= 3, label)
  }
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

test('two hospitals with different approved primaries do not render the same ramp', () => {
  const gold = buildClinicThemeStyle({ brand_primary_color: '#D6A72C', brand_accent_color: null })
  const navy = buildClinicThemeStyle({ brand_primary_color: '#17365D', brand_accent_color: null })

  const distinguishing: Array<`--clinic-${string}`> = [
    '--clinic-brand-action',
    '--clinic-brand-soft',
    '--clinic-line',
  ]
  for (const token of distinguishing) {
    assert.notEqual(gold[token], navy[token], `${token}이 병원별로 갈리지 않는다`)
  }
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
