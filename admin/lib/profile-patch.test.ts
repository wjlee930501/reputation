import assert from 'node:assert/strict'
import test from 'node:test'

import {
  BRAND_PATCH_FIELDS,
  FACTS_PATCH_FIELDS,
  brandPatchPayload,
  factsPatchPayload,
  profilePatchPayload,
} from './profile-patch.ts'

const snapshot = {
  // 헤더 스냅샷이 들고 오는 것 전부 — 폼 상태에 그대로 섞여 들어온다.
  id: 'hospital-1',
  name: '테스트의원',
  slug: 'test-clinic',
  status: 'ACTIVE',
  profile_complete: true,
  logo_url: 'gs://reputation-images/assets/hospital/logo.png',
  specialties: ['정형외과'],
  address: '서울 성동구',
  address_detail: '3층 301호',
  physicians: [{ name: '김민수', display_order: 0, is_representative: true }],
  // 서버가 대표 의료진에서 파생하는 값 — 폼이 되돌려 보내면 안 된다.
  director_name: '옛 원장',
  director_career: '옛 약력',
  brand_primary_color: '#17365D',
  hero_headline: '오늘도 문 여는 동네 주치의',
}

test('사실 저장은 사실 칸만 보낸다 — 브랜드 값은 실리지 않는다', () => {
  const payload = factsPatchPayload(snapshot)

  assert.deepEqual(payload, {
    specialties: ['정형외과'],
    address: '서울 성동구',
    address_detail: '3층 301호',
  })
  for (const field of BRAND_PATCH_FIELDS) {
    assert.equal(field in payload, false, `${field}을 사실 저장이 덮어쓴다`)
  }
})

test('의료진 목록은 허용 목록이 아니라 목록을 읽은 화면이 싣는다', () => {
  // 집합 전체 교체라, 아직 못 읽은 화면의 빈 목록이 기존 의료진을 지우면 안 된다.
  assert.equal(FACTS_PATCH_FIELDS.includes('physicians' as never), false)
  assert.equal('physicians' in factsPatchPayload(snapshot), false)
  assert.equal('physicians' in profilePatchPayload(snapshot), false)
})

test('대표 이미지 주소는 브랜드 저장이 소유하지 않는다', () => {
  // 사진 목록의 ‘대표 이미지로 지정’만 hero_image_url을 쓴다 — 브랜드 폼 스냅샷이
  // 되돌려 보내면 방금 지정한 사진을 옛 값으로 덮어쓴다.
  assert.equal(BRAND_PATCH_FIELDS.includes('hero_image_url' as never), false)
  const payload = brandPatchPayload({ ...snapshot, hero_image_url: '/assets/hospital/old.jpg' })
  assert.equal('hero_image_url' in payload, false)
  assert.equal('hero_image_url' in profilePatchPayload({ ...snapshot, hero_image_url: '/a.jpg' }), false)
})

test('파생되는 원장 표시값과 사라진 자격 칸은 저장 본문에 없다', () => {
  const payload = factsPatchPayload(snapshot)
  for (const field of ['director_name', 'director_career', 'director_credentials']) {
    assert.equal(field in payload, false, `${field}을 폼이 되돌려 보낸다`)
  }
})

test('브랜드 저장은 브랜드 칸만 보낸다 — 사실 값은 실리지 않는다', () => {
  const payload = brandPatchPayload(snapshot)

  assert.deepEqual(payload, {
    brand_primary_color: '#17365D',
    hero_headline: '오늘도 문 여는 동네 주치의',
  })
  for (const field of FACTS_PATCH_FIELDS) {
    assert.equal(field in payload, false, `${field}을 브랜드 저장이 덮어쓴다`)
  }
})

test('어느 목록에도 완료 플래그·로고·병원 식별자는 없다', () => {
  for (const payload of [
    factsPatchPayload(snapshot),
    brandPatchPayload(snapshot),
    profilePatchPayload(snapshot),
  ]) {
    // 완료는 서버가 파생하고, 로고는 업로드가 소유한다. 서버는 모르는 필드를 422로 되돌린다.
    assert.equal('profile_complete' in payload, false)
    assert.equal('logo_url' in payload, false)
    assert.equal('name' in payload, false)
    assert.equal('id' in payload, false)
    assert.equal('slug' in payload, false)
    assert.equal('status' in payload, false)
  }
})

test('한 폼이 둘 다 편집하는 예전 화면은 두 목록의 합집합을 보낸다', () => {
  assert.deepEqual(profilePatchPayload(snapshot), {
    specialties: ['정형외과'],
    address: '서울 성동구',
    address_detail: '3층 301호',
    brand_primary_color: '#17365D',
    hero_headline: '오늘도 문 여는 동네 주치의',
  })
})

test('두 목록은 겹치지 않는다 — 겹치면 한쪽 저장이 다른 쪽을 덮는다', () => {
  const overlap = FACTS_PATCH_FIELDS.filter((field) =>
    (BRAND_PATCH_FIELDS as readonly string[]).includes(field),
  )
  assert.deepEqual(overlap, [])
})
