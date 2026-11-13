import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isSiteLayout,
  layoutDistributionLine,
  layoutDistributionPath,
  readLayoutDistribution,
  sameRegionBadge,
} from './site-layout.ts'

test('only A, B and C are layouts', () => {
  assert.equal(isSiteLayout('A'), true)
  assert.equal(isSiteLayout('C'), true)
  assert.equal(isSiteLayout('D'), false)
  assert.equal(isSiteLayout(null), false)
  assert.equal(isSiteLayout('a'), false)
})

test('the distribution path carries address, every region entry and the hospital to skip', () => {
  assert.equal(
    layoutDistributionPath({ address: ' 대구광역시 수성구 달구벌대로 3218 ', excludeHospitalId: 'h1' }),
    '/admin/hospitals/site-layout-distribution?address=%EB%8C%80%EA%B5%AC%EA%B4%91%EC%97%AD%EC%8B%9C+%EC%88%98%EC%84%B1%EA%B5%AC+%EB%8B%AC%EA%B5%AC%EB%B2%8C%EB%8C%80%EB%A1%9C+3218&exclude_hospital_id=h1',
  )
  assert.equal(
    layoutDistributionPath({ region: ['경산', '', null, '대구'] }),
    '/admin/hospitals/site-layout-distribution?region=%EA%B2%BD%EC%82%B0&region=%EB%8C%80%EA%B5%AC',
  )
  assert.equal(layoutDistributionPath({}), '/admin/hospitals/site-layout-distribution')
})

test('a distribution response is read only when its shape is complete', () => {
  const good = { province: '대구', counts: { A: 2, B: 1, C: 0, unset: 4 }, total: 7 }
  assert.deepEqual(readLayoutDistribution(good), good)
  assert.deepEqual(readLayoutDistribution({ ...good, province: null, total: 0 })?.province, null)
  assert.equal(readLayoutDistribution({ ...good, counts: { A: 2, B: 1, C: 0 } }), null)
  assert.equal(readLayoutDistribution({ ...good, counts: { ...good.counts, A: -1 } }), null)
  assert.equal(readLayoutDistribution({ ...good, total: '7' }), null)
  assert.equal(readLayoutDistribution(null), null)
})

test('the distribution line names the province, the total and every layout', () => {
  const line = layoutDistributionLine({ province: '대구', counts: { A: 2, B: 1, C: 0, unset: 4 }, total: 7 })
  assert.equal(line, '대구 공개 병원 7곳 — A 2 · B 1 · C 0 · 고르기 전 4')
  assert.equal(
    layoutDistributionLine({ province: '대구', counts: { A: 1, B: 0, C: 0, unset: 0 }, total: 1 }),
    '대구 공개 병원 1곳 — A 1 · B 0 · C 0',
  )
  assert.match(layoutDistributionLine({ province: '제주', counts: { A: 0, B: 0, C: 0, unset: 0 }, total: 0 }), /제주에 공개 중인 다른 병원이 아직 없습니다/)
  assert.match(layoutDistributionLine({ province: null, counts: { A: 0, B: 0, C: 0, unset: 0 }, total: 0 }), /지역을 읽지 못해/)
})

test('a card badge appears only for a layout already used in the same province', () => {
  const distribution = { province: '대구', counts: { A: 2, B: 0, C: 1, unset: 3 }, total: 6 }
  assert.equal(sameRegionBadge(distribution, 'A'), '대구 2곳 사용 중')
  assert.equal(sameRegionBadge(distribution, 'B'), null)
  assert.equal(sameRegionBadge(null, 'A'), null)
  assert.equal(sameRegionBadge({ ...distribution, province: null }, 'A'), null)
})
