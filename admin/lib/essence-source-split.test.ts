import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  describePhotoSourceExclusion,
  isPhotoSource,
  splitEssenceSources,
} from './essence-source-split.ts'

const sources = [
  { source_type: 'INTERVIEW', status: 'PROCESSED', evidence_note_count: 8 },
  { source_type: 'NAVER_BLOG', status: 'PENDING', evidence_note_count: 0 },
  { source_type: 'PHOTO_DOCTOR', status: 'PENDING', evidence_note_count: 0 },
  { source_type: 'PHOTO_CLINIC_EXTERIOR', status: 'PROCESSED', evidence_note_count: 0 },
  { source_type: 'HOMEPAGE', status: 'PROCESSED', evidence_note_count: 5 },
]

test('every photo category is recognised as a photo, not as an evidence source', () => {
  for (const type of [
    'PHOTO_DOCTOR',
    'PHOTO_CLINIC_EXTERIOR',
    'PHOTO_CLINIC_INTERIOR',
    'PHOTO_TREATMENT_ROOM',
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

test('the screen says where the photos went instead of silently dropping them', () => {
  assert.equal(describePhotoSourceExclusion(0), null)
  assert.match(describePhotoSourceExclusion(6) ?? '', /사진 6장은 공개 표면용 자산/)
})

test('the standards screen takes its denominators from the split', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/essence/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /splitEssenceSources/)
  assert.match(page, /describePhotoSourceExclusion/)
  // 표가 다시 사진까지 훑으면 실패한다.
  assert.doesNotMatch(page, /\{sources\.map\(\(source\)/)
})
