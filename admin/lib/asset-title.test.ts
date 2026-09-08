import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { defaultAssetTitle, defaultAssetTitles, duplicateAssetTitles } from './asset-title.ts'

const photosSection = readFileSync(
  new URL('../app/hospitals/[id]/info/PhotosSection.tsx', import.meta.url),
  'utf8',
)

// 규칙은 백엔드 `_filename_without_extension`과 같아야 한다. 어긋나면 화면이 보여준
// 기본 제목과 실제로 저장되는 제목이 달라진다.
test('defaultAssetTitle strips the path and the extension', () => {
  assert.equal(defaultAssetTitle('photo.jpg'), 'photo')
  assert.equal(defaultAssetTitle('/path/to/photo.jpg'), 'photo')
  assert.equal(defaultAssetTitle('C:\\Users\\doctor\\photo.jpg'), 'photo')
  assert.equal(defaultAssetTitle('clinic.exterior.2024.jpg'), 'clinic.exterior.2024')
  assert.equal(defaultAssetTitle('photo'), 'photo')
  assert.equal(defaultAssetTitle('병원외관.jpg'), '병원외관')
  assert.equal(defaultAssetTitle(''), '')
})

test('defaultAssetTitle truncates to the stored column length', () => {
  assert.equal(defaultAssetTitle(`${'a'.repeat(350)}.jpg`).length, 300)
})

test('defaultAssetTitle leaves a dot-leading name empty so the server names it', () => {
  // 백엔드 `_filename_without_extension('.hidden')`도 빈 문자열을 준다. 빈 제목은
  // 업로드 시 서버가 기본값으로 채우므로, 화면과 저장 결과가 어긋나지 않는다.
  assert.equal(defaultAssetTitle('.hidden'), '')
})

// D-1의 핵심: 파일이 서로 다르면 기본 제목도 서로 달라야 한다.
test('a batch of files gets one distinct default title per file', () => {
  assert.deepEqual(
    defaultAssetTitles(['외관.jpg', '진료실.jpg', '대기실.png']),
    ['외관', '진료실', '대기실'],
  )
})

test('duplicateAssetTitles names only the titles that actually collide', () => {
  assert.deepEqual(duplicateAssetTitles(['병원 사진', '병원 사진', '진료실']), ['병원 사진'])
  assert.deepEqual(duplicateAssetTitles(['외관', '진료실', '대기실']), [])
})

test('duplicateAssetTitles treats case and surrounding spaces as the same caption', () => {
  assert.deepEqual(duplicateAssetTitles(['Clinic', ' clinic ']), ['Clinic'])
})

test('duplicateAssetTitles ignores blank titles that the server will fill in', () => {
  assert.deepEqual(duplicateAssetTitles(['', '   ', '']), [])
})

test('the upload form sends the per-file title rather than one shared title', () => {
  assert.match(photosSection, /setTitles\(defaultAssetTitles\(/)
  assert.match(photosSection, /title: titles\[i\] \?\? '',/)
  // 공통 제목 상태가 남아 있으면 모든 파일이 다시 같은 제목으로 저장된다.
  assert.doesNotMatch(photosSection, /const \[title, setTitle\]/)
  assert.match(photosSection, /id=\{`info-photo-title-\$\{index\}`\}/)
})
