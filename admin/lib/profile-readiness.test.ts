import assert from 'node:assert/strict'
import test from 'node:test'

import { buildProfileChecklist, isProfileReady, missingRequiredProfileItems } from './profile-readiness.ts'

const completeProfile = {
  director_name: '이원장',
  director_career: '외과 전문의',
  director_philosophy: '충분히 설명합니다.',
  address: '수원시 팔달구',
  phone: '031-000-0000',
  business_hours: { mon: '09:00-18:00' },
  website_url: 'https://clinic.example',
  naver_place_url: 'https://naver.me/example',
  google_maps_url: 'https://maps.google.com/example',
  latitude: 37.1,
  longitude: 127.1,
  region: ['수원'],
  specialties: ['외과'],
  keywords: ['대장항문'],
  treatments: [{ name: '치질 진료' }],
}

test('strict profile readiness accepts a fully populated profile', () => {
  assert.equal(isProfileReady(completeProfile), true)
  assert.deepEqual(missingRequiredProfileItems(completeProfile), [])
})

test('every required group is a hard gate', () => {
  const cases: Array<[string, Record<string, unknown>]> = [
    ['director_basic', { director_career: '   ' }],
    ['director_philosophy', { director_philosophy: '' }],
    ['contact', { business_hours: { mon: ' ' } }],
    ['web_channels', { website_url: '', blog_url: '' }],
    ['ai_channels', { naver_place_url: '' }],
    ['geo', { longitude: Number.NaN }],
    ['targeting', { keywords: [] }],
    ['treatments', { treatments: [{ name: '  ' }] }],
  ]

  for (const [expectedKey, patch] of cases) {
    const missing = missingRequiredProfileItems({ ...completeProfile, ...patch })
    assert.ok(missing.some((item) => item.key === expectedKey), `${expectedKey} should be missing`)
  }
})

test('recommended competitor and custom domain do not block completion', () => {
  const checklist = buildProfileChecklist(completeProfile)
  assert.equal(isProfileReady(completeProfile), true)
  assert.equal(checklist.find((item) => item.key === 'competitors')?.status, 'recommended')
  assert.equal(checklist.find((item) => item.key === 'domain')?.status, 'recommended')
})

test('coordinates accept geographic boundaries and reject out-of-range values', () => {
  assert.equal(isProfileReady({ ...completeProfile, latitude: -90, longitude: 180 }), true)
  assert.equal(isProfileReady({ ...completeProfile, latitude: 90, longitude: -180 }), true)
  assert.equal(
    missingRequiredProfileItems({ ...completeProfile, latitude: 90.0001 }).some((item) => item.key === 'geo'),
    true,
  )
  assert.equal(
    missingRequiredProfileItems({ ...completeProfile, longitude: -180.0001 }).some((item) => item.key === 'geo'),
    true,
  )
})
