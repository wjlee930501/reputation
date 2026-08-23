import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildClinicVisualChecklist,
  clinicVisualSummary,
  isApprovedAccessMode,
  isApprovedBrandColor,
  isClinicVisualApproved,
  isExternalLogo,
  isServableLogo,
  missingClinicVisualItems,
} from './clinic-visual-readiness.ts'

const approved = {
  // 업로드된 자산 참조. 외부 주소는 공개 화면이 쓰지 못하므로 승인으로 치지 않는다.
  logo_url: 'gs://reputation-images/assets/abc/logo.png',
  brand_primary_color: '#17365D',
  hero_headline: '오늘도 문 여는 동네 주치의',
  hero_description: '증상과 진료 정보를 방문 전에 확인하세요.',
  site_access_mode: 'urgent',
}

test('a fully approved visual profile has nothing left to confirm', () => {
  assert.equal(isClinicVisualApproved(approved), true)
  assert.deepEqual(missingClinicVisualItems(approved), [])
  assert.equal(clinicVisualSummary(approved), '공개 표면 시각 요소 승인 완료')
})

test('an empty profile names every visual item the AE still has to approve', () => {
  const missing = missingClinicVisualItems({}).map((item) => item.key)

  assert.deepEqual(missing, ['logo', 'primary_color', 'hero_copy', 'access_mode'])
})

test('photos never gate visual approval', () => {
  // 사진이 한 장도 없는 병원도 시각 승인은 통과해야 한다.
  assert.equal(isClinicVisualApproved({ ...approved, photo_count: 0 }), true)

  const photoItem = buildClinicVisualChecklist({ photo_count: 0 }).find(
    (item) => item.key === 'photos',
  )
  assert.equal(photoItem?.status, 'optional')
  assert.equal(photoItem?.blocksApproval, false)
  assert.ok(!missingClinicVisualItems({ photo_count: 0 }).some((item) => item.key === 'photos'))
})

test('only one primary color is required — an accent is never asked for', () => {
  const keys = buildClinicVisualChecklist({}).map((item) => item.key)

  assert.ok(keys.includes('primary_color'))
  assert.ok(!keys.some((key) => String(key).includes('accent')))
  assert.equal(isClinicVisualApproved({ ...approved, brand_accent_color: null }), true)
})

test('brand color approval only accepts a full hex the public surface can derive from', () => {
  assert.equal(isApprovedBrandColor('#17365D'), true)
  assert.equal(isApprovedBrandColor('#17365d'), true)
  assert.equal(isApprovedBrandColor(' #17365D '), true)
  assert.equal(isApprovedBrandColor('navy'), false)
  assert.equal(isApprovedBrandColor('#173'), false)
  assert.equal(isApprovedBrandColor(''), false)
  assert.equal(isApprovedBrandColor(null), false)
})

test('access mode approval rejects anything the public surface cannot honour', () => {
  for (const mode of ['urgent', 'appointment', 'specialist']) {
    assert.equal(isApprovedAccessMode(mode), true)
  }
  assert.equal(isApprovedAccessMode(''), false)
  assert.equal(isApprovedAccessMode('walk-in'), false)
  assert.equal(isApprovedAccessMode(null), false)
})

test('either hero line counts as approved copy', () => {
  assert.equal(isClinicVisualApproved({ ...approved, hero_description: null }), true)
  assert.equal(isClinicVisualApproved({ ...approved, hero_headline: null }), true)
  assert.equal(
    isClinicVisualApproved({ ...approved, hero_headline: '  ', hero_description: '' }),
    false,
  )
})

test('the summary lists the outstanding items so the AE knows what to fill', () => {
  const summary = clinicVisualSummary({ ...approved, logo_url: null, site_access_mode: null })

  assert.equal(summary, '승인 필요: 공식 로고, 첫 화면 정보 우선순위')
})


test('an external logo URL is stored but can never reach the screen, so it is not approved', () => {
  // 공개 표면은 우리 저장소·오리진이 아닌 주소를 쓰지 않는다. 값이 있다는 이유로
  // `승인됨`을 주면 운영자는 로고가 빠진 사이트를 정상으로 알고 넘어간다(O-5).
  const external = { ...approved, logo_url: 'https://cdn.imweb.me/thumbnail/logo.png' }

  assert.equal(isServableLogo(external.logo_url), false)
  assert.equal(isExternalLogo(external.logo_url), true)
  assert.equal(isClinicVisualApproved(external), false)
  assert.deepEqual(
    missingClinicVisualItems(external).map((item) => item.key),
    ['logo'],
  )

  // 왜 승인이 안 되는지 화면에서 읽을 수 있어야 한다.
  const logoItem = buildClinicVisualChecklist(external).find((item) => item.key === 'logo')
  assert.match(logoItem!.hint, /업로드/)
})

test('uploaded asset refs in every supported form count as servable', () => {
  for (const ref of ['gs://bucket/a.png', 'local://abc/a.png', '/assets/abc/a.png']) {
    assert.equal(isServableLogo(ref), true, ref)
    assert.equal(isExternalLogo(ref), false, ref)
  }
  assert.equal(isServableLogo(null), false)
  assert.equal(isExternalLogo(null), false)
})
