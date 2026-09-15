import assert from 'node:assert/strict'
import test from 'node:test'

import { buildPostalAddress, fullClinicAddress } from './clinic-schema.ts'

test('buildPostalAddress preserves the verified physical address without target regions', () => {
  assert.deepEqual(buildPostalAddress(' 서울특별시 강남구 테헤란로 1 '), {
    '@type': 'PostalAddress',
    streetAddress: '서울특별시 강남구 테헤란로 1',
    addressCountry: 'KR',
  })
})

test('buildPostalAddress omits an unknown physical address', () => {
  assert.equal(buildPostalAddress('  '), undefined)
  assert.equal(buildPostalAddress(null), undefined)
})

test('the building detail rides inside streetAddress, not a new invented field', () => {
  // schema.org에는 건물명·층·호를 담는 별도 필드가 없다. 도로명만 내보내면 지도가
  // 건물 앞까지만 안내하고 환자는 몇 층인지 모른 채 도착한다.
  assert.deepEqual(buildPostalAddress('서울특별시 강남구 테헤란로 1', ' 리츠빌딩 4층 401호 '), {
    '@type': 'PostalAddress',
    streetAddress: '서울특별시 강남구 테헤란로 1 리츠빌딩 4층 401호',
    addressCountry: 'KR',
  })
  // 상세 주소가 비어 있으면 도로명만 남고 꼬리 공백이 붙지 않는다.
  assert.equal(
    buildPostalAddress('서울특별시 강남구 테헤란로 1', '   ')?.streetAddress,
    '서울특별시 강남구 테헤란로 1',
  )
})

test('the screen and the structured data print the same full address', () => {
  const road = '서울특별시 강남구 테헤란로 1'
  const detail = '리츠빌딩 4층'
  assert.equal(fullClinicAddress(road, detail), buildPostalAddress(road, detail)?.streetAddress)
  assert.equal(fullClinicAddress(road, null), road)
  assert.equal(fullClinicAddress('', detail), '')
})
