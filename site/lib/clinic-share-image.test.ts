// 공유(OG) 이미지의 대체 글은 실제로 고른 사진을 말한다 — 원장 사진일 때만 원장 이름.
import assert from 'node:assert/strict'
import test from 'node:test'

import { PLATFORM_SHARE_IMAGE, selectShareImage } from './clinic-share-image.ts'

const DOCTOR_PHOTO = {
  id: 'doctor-real',
  source_type: 'PHOTO_DOCTOR',
  title: '원장 진료 사진',
  url: '/api/v1/public/demo/assets/doctor-real',
  asset_kind: 'VERIFIED_REAL_PERSON',
  approved_usage: ['DOCTOR_IDENTITY'],
}
const ILLUSTRATION = {
  id: 'drawing',
  source_type: 'PHOTO_DOCTOR',
  title: '원장 캐릭터 일러스트',
  url: '/api/v1/public/demo/assets/drawing',
  asset_kind: 'EDITORIAL_GRAPHIC',
  approved_usage: ['CONTENT_EDITORIAL'],
}
const FACILITY = { id: 'lobby', source_type: 'PHOTO_FACILITY', title: '대기실', url: '/api/v1/public/demo/assets/lobby' }

function hospital(photos: unknown[], shareId: string | null = null) {
  return {
    name: '데모내과의원',
    slug: 'demo',
    director_name: '김데모',
    director_photo_url: null,
    photos,
    site_config: shareId ? { share: { imageAssetId: shareId, description: null } } : null,
  } as unknown as Parameters<typeof selectShareImage>[0]
}

test('a verified director photo carries the director name', () => {
  const image = selectShareImage(hospital([DOCTOR_PHOTO]))
  assert.match(image.url, /doctor-real$/)
  assert.equal(image.alt, '김데모 원장')
})

test('the platform fallback (director photo not publishable) is named for the hospital, not the director', () => {
  const image = selectShareImage(hospital([ILLUSTRATION]))
  assert.equal(image.url, PLATFORM_SHARE_IMAGE)
  assert.equal(image.alt, '데모내과의원')
})

test('a share photo chosen in the section settings is named for the hospital', () => {
  const image = selectShareImage(hospital([DOCTOR_PHOTO, FACILITY], 'lobby'))
  assert.match(image.url, /lobby$/)
  assert.equal(image.alt, '데모내과의원')
})
