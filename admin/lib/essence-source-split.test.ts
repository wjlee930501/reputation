import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  describePhotoSourceExclusion,
  isBlankText,
  isPhotoSource,
  isRequiredTextSource,
  splitEssenceSources,
} from './essence-source-split.ts'

const sources = [
  { source_type: 'INTERVIEW', status: 'PROCESSED', raw_text: '원장 인터뷰 원문', evidence_note_count: 8 },
  { source_type: 'NAVER_BLOG', status: 'PENDING', raw_text: '블로그 본문', evidence_note_count: 0 },
  { source_type: 'PHOTO_DOCTOR', status: 'PENDING', evidence_note_count: 0 },
  { source_type: 'PHOTO_CLINIC_EXTERIOR', status: 'PROCESSED', evidence_note_count: 0 },
  { source_type: 'HOMEPAGE', status: 'PROCESSED', raw_text: '홈페이지 본문', evidence_note_count: 5 },
]

test('every photo category is recognised as a photo, not as an evidence source', () => {
  for (const type of [
    'PHOTO_DOCTOR',
    'PHOTO_CLINIC_EXTERIOR',
    'PHOTO_CLINIC_INTERIOR',
    'PHOTO_TREATMENT_ROOM',
    'PHOTO_BRAND',
  ]) {
    assert.equal(isPhotoSource({ source_type: type }), true, type)
  }
  assert.equal(isPhotoSource({ source_type: 'INTERVIEW' }), false)
})

test('the evidence table and the processed ratio both leave photos out', () => {
  const split = splitEssenceSources(sources)

  assert.equal(split.textSourceCount, 3)
  assert.equal(split.processedTextCount, 2)
  assert.equal(split.photoSources.length, 2)
  assert.deepEqual(
    split.textSources.map((source) => source.source_type),
    ['INTERVIEW', 'NAVER_BLOG', 'HOMEPAGE'],
  )
})

test('a processed photo never inflates the processed evidence count', () => {
  const split = splitEssenceSources(sources)

  // PHOTO_CLINIC_EXTERIOR가 PROCESSED지만 근거 처리 집계에는 들어가지 않는다.
  assert.equal(split.processedTextCount, 2)
  assert.notEqual(split.processedTextCount, 3)
})

test('the total of extracted evidence counts only text sources', () => {
  const split = splitEssenceSources([
    ...sources,
    { source_type: 'PHOTO_DOCTOR', status: 'PROCESSED', evidence_note_count: 4 },
  ])

  assert.equal(split.evidenceNoteCount, 13)
})

test('a hospital with photos only has an empty evidence denominator, not a full one', () => {
  const split = splitEssenceSources([
    { source_type: 'PHOTO_DOCTOR', status: 'PROCESSED', evidence_note_count: 0 },
    { source_type: 'PHOTO_TREATMENT_ROOM', status: 'PROCESSED', evidence_note_count: 0 },
  ])

  assert.equal(split.textSourceCount, 0)
  assert.equal(split.processedTextCount, 0)
})

test('the blank-text rule is the server\'s whitespace set, not JS trim()', () => {
  assert.equal(isBlankText(''), true)
  // U+FEFF는 trim()이 깎지만 Python str.strip()은 남긴다 — 서버 기준으로 "원문 있음"이다.
  assert.equal(isBlankText('\ufeff'), false)
  assert.equal(isBlankText('   \u3000'), true)
  assert.equal(isBlankText('a'), false)
})

test('the required-source rule matches the server: excluded, photo and text-less rows are out', () => {
  assert.equal(
    isRequiredTextSource({ source_type: 'INTERVIEW', status: 'PENDING', raw_text: '원장 인터뷰 원문' }),
    true,
  )
  // URL만 있고 본문이 없는 자료 — 서버는 [근거 추출]을 400으로 돌려보낸다.
  assert.equal(
    isRequiredTextSource({ source_type: 'HOMEPAGE', status: 'PENDING', raw_text: null }),
    false,
  )
  assert.equal(
    isRequiredTextSource({ source_type: 'HOMEPAGE', status: 'PENDING', raw_text: '   ' }),
    false,
  )
  assert.equal(
    isRequiredTextSource({ source_type: 'INTERVIEW', status: 'EXCLUDED', raw_text: '원문' }),
    false,
  )
  assert.equal(
    isRequiredTextSource({ source_type: 'PHOTO_DOCTOR', status: 'PROCESSED', raw_text: '캡션' }),
    false,
  )
})

test('the denominator drops the sources the server never requires', () => {
  const split = splitEssenceSources([
    ...sources,
    { source_type: 'HOMEPAGE', status: 'PENDING', raw_text: null, evidence_note_count: 0 },
    { source_type: 'BROCHURE', status: 'EXCLUDED', raw_text: '제외한 원문', evidence_note_count: 0 },
  ])

  // 표에는 5줄이 남지만 분모는 서버가 세는 3개 그대로다.
  assert.equal(split.textSources.length, 5)
  assert.equal(split.textSourceCount, 3)
  assert.equal(split.processedTextCount, 2)
})

test('the screen says where the photos went instead of silently dropping them', () => {
  assert.equal(describePhotoSourceExclusion(0), null)
  assert.match(describePhotoSourceExclusion(6) ?? '', /사진 6장은 공개 표면용 자산/)
})
