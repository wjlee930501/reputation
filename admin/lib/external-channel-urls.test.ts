import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  GOOGLE_CHANNEL_FIELD_HINTS,
  findDuplicateChannelUrls,
  normalizeChannelUrl,
} from './external-channel-urls.ts'

test('the same map link pasted into both Google fields is reported by name', () => {
  const warnings = findDuplicateChannelUrls({
    google_business_profile_url: 'https://maps.google.com/place/abc',
    google_maps_url: 'https://maps.google.com/place/abc',
  })

  assert.equal(warnings.length, 1)
  assert.match(warnings[0], /구글 병원 정보 URL · 구글 지도 URL/)
})

test('scheme, www and a trailing slash do not hide a duplicate', () => {
  const warnings = findDuplicateChannelUrls({
    google_maps_url: 'https://www.maps.google.com/place/abc/',
    naver_place_url: 'http://maps.google.com/place/abc',
  })

  assert.equal(warnings.length, 1)
})

test('different addresses produce no warning', () => {
  assert.deepEqual(
    findDuplicateChannelUrls({
      google_business_profile_url: 'https://business.google.com/dashboard/l/1',
      google_maps_url: 'https://maps.google.com/place/abc',
      naver_place_url: 'https://naver.me/xyz',
    }),
    [],
  )
})

test('empty fields are never counted as sharing an address', () => {
  assert.deepEqual(
    findDuplicateChannelUrls({
      google_business_profile_url: '',
      google_maps_url: '   ',
      naver_place_url: null,
      kakao_channel_url: undefined,
    }),
    [],
  )
})

test('three fields sharing one address are reported once, not three times', () => {
  const warnings = findDuplicateChannelUrls({
    google_business_profile_url: 'https://maps.google.com/p/1',
    google_maps_url: 'https://maps.google.com/p/1',
    website_url: 'https://maps.google.com/p/1',
  })

  assert.equal(warnings.length, 1)
  assert.match(warnings[0], /홈페이지 URL/)
})

test('normalizeChannelUrl keeps the path, which is what distinguishes two places', () => {
  assert.equal(normalizeChannelUrl('HTTPS://Maps.Google.com/Place/ABC/'), 'maps.google.com/place/abc')
  assert.equal(normalizeChannelUrl(null), null)
})

test('the profile screen explains what each Google field holds and warns on duplicates', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/profile/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /findDuplicateChannelUrls/)
  assert.match(page, /GOOGLE_CHANNEL_FIELD_HINTS/)
  assert.match(GOOGLE_CHANNEL_FIELD_HINTS.google_maps_url, /환자가 길찾기/)
})
