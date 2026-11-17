import assert from 'node:assert/strict'
import test from 'node:test'

import { regionSentenceLabel } from './clinic-region.ts'

test('문장에는 앞의 대표 지역 두 개만 쓰고 광역 이름은 줄인다', () => {
  const seogwipo = [
    '제주특별자치도 서귀포시', '법환동', '서귀포', '제주', '서귀포 신시가지', '토평동', '동홍동',
    '대륜동', '호근동', '삼화지구', '아라동', '연동', '서호동',
  ]
  assert.equal(regionSentenceLabel(seogwipo), '제주 서귀포시 법환동')
})

test('운영 병원의 지역 태그 앞부분이 자연스러운 문장 지역이 된다', () => {
  assert.equal(regionSentenceLabel(['마포구', '도화동', '마포역']), '마포구 도화동')
  assert.equal(regionSentenceLabel(['대구 동구', '율하동', '안심']), '대구 동구 율하동')
  assert.equal(regionSentenceLabel(['수원시 팔달구']), '수원시 팔달구')
  assert.equal(regionSentenceLabel(['부산 남구', '대연동', '대연역']), '부산 남구 대연동')
})

test('같은 지역을 두 번 말하는 태그는 건너뛴다', () => {
  assert.equal(regionSentenceLabel(['세종특별자치시', '세종시', '나성동']), '세종 나성동')
  assert.equal(regionSentenceLabel(['경산', '경산시', '사동']), '경산 사동')
})

test('빈 지역은 빈 문자열', () => {
  assert.equal(regionSentenceLabel([]), '')
  assert.equal(regionSentenceLabel(null), '')
  assert.equal(regionSentenceLabel(['  ', '']), '')
})
