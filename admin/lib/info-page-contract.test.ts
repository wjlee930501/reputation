import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { brandDefaultsNotice } from './info-sections.ts'

function infoFile(name: string): string {
  return readFileSync(new URL(`../app/hospitals/[id]/info/${name}`, import.meta.url), 'utf8')
}

const infoPage = infoFile('page.tsx')
const factsSection = infoFile('FactsSection.tsx')
const brandSection = infoFile('BrandSection.tsx')
const photosSection = infoFile('PhotosSection.tsx')
// 브랜드 섹션이 실제로 그리는 것 전부 — 칸이 다른 파일로 빠졌다고 계약이 느슨해지면 안 된다.
const brandSources = [
  brandSection,
  infoFile('ClinicVisualForm.tsx'),
  infoFile('ClinicLogoField.tsx'),
].join('\n')
const hospitalLayout = readFileSync(
  new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
  'utf8',
)
const infoSources = `${infoPage}\n${factsSection}`

test('남은 필수 항목은 서버 판정을 그대로 읽는다', () => {
  assert.match(infoPage, /missing_profile_requirements/)
  assert.match(infoPage, /remainingRequirementsSummary/)
})

test('저장 본문은 기존 프로필 PATCH 규칙을 재사용한다', () => {
  assert.match(infoPage, /profilePatchPayload\(/)
  assert.match(infoPage, /geocode_address/)
  assert.match(infoPage, /REGION_KEYWORD_MIXED/)
  assert.match(infoPage, /profileSaveErrorMessage/)
})

test('완료 여부는 사람이 표시하지 않는다 — 체크박스도 클라이언트 체크리스트도 없다', () => {
  assert.doesNotMatch(infoSources, /profile_complete/)
  assert.doesNotMatch(infoSources, /type="checkbox"/)
  assert.doesNotMatch(infoSources, /buildProfileChecklist/)
  assert.doesNotMatch(infoSources, /data-profile-completion/)
  assert.doesNotMatch(infoSources, /온보딩 완료/)
})

test('자료 등록·처리 버튼은 이 화면에 없다', () => {
  assert.doesNotMatch(infoSources, /자료로 추가/)
  assert.doesNotMatch(infoSources, /CrawlForm/)
  assert.doesNotMatch(infoSources, /처리 시작/)
})

test('다섯 섹션이 각자의 앵커를 가진다', () => {
  assert.match(factsSection, /id=\{`info-\$\{section\}`\}/)
  for (const section of ['director', 'treatments', 'contact', 'channels', 'targeting']) {
    assert.match(factsSection, new RegExp(`section="${section}"`))
  }
})

test('병원 상세 탭에 병원 정보가 있다', () => {
  assert.match(hospitalLayout, /\{ label: '병원 정보', path: 'info'/)
})

test('브랜드 섹션은 온보딩과 같은 칸을 쓰고 로고는 업로드로 받는다', () => {
  for (const field of [
    'brand_primary_color',
    'site_access_mode',
    'hero_headline',
    'hero_description',
  ]) {
    assert.match(brandSources, new RegExp(`${field}:`), `${field}을 저장하지 않는다`)
  }
  assert.match(brandSources, /`\/admin\/hospitals\/\$\{hospitalId\}\/logo`[\s\S]{0,60}method: 'POST'/)
})

test('비어 있는 브랜드 값은 승인 대기가 아니라 기본값 사용 중으로 말한다', () => {
  assert.match(brandSection, /visual_approval_missing/)
  assert.match(brandSection, /brandDefaultsNotice/)
  assert.match(brandDefaultsNotice(['공식 로고']) ?? '', /기본값 사용 중/)
  // 승인 엔드포인트는 없다 — 저장이 곧 승인이므로 배지도 버튼도 만들지 않는다.
  assert.doesNotMatch(brandSources, /승인 필요|승인됨/)
})

test('자주 쓰지 않는 브랜드 칸은 고급으로 접어 둔다', () => {
  const advanced = brandSection.slice(brandSection.indexOf('<details'))
  assert.ok(advanced.length > 0, '<details>를 찾지 못했다')
  for (const field of [
    'brand_accent_color',
    'hero_image_url',
    'hero_media_kind',
    'image_style_direction',
  ]) {
    assert.match(advanced, new RegExp(field), `${field}이 고급 안에 없다`)
  }
})

test('사진 업로드는 공개 여부와 권리 3필드를 같은 요청에 보낸다', () => {
  assert.match(photosSection, /photoUploadFormData\(/)
  assert.match(photosSection, /sources\/upload/)
  // FormData 키는 순수 함수가 만든다 — 화면이 몰래 다른 이름을 붙이지 않는다.
  const builder = readFileSync(new URL('./info-sections.ts', import.meta.url), 'utf8')
  for (const key of [
    'photo_source_owner',
    'photo_rights_basis',
    'photo_evidence_reference',
    'is_public',
  ]) {
    assert.match(builder, new RegExp(`append\\('${key}'`), `${key}을 보내지 않는다`)
  }
})

test('사진 행은 공유 게이트가 판정하고 허용 용도 select를 다시 묻지 않는다', () => {
  assert.match(photosSection, /PHOTO_PUBLIC_GATE_COPY/)
  assert.match(photosSection, /describePhotoPublicGate\(photo\)/)
  assert.match(
    photosSection,
    /`\/admin\/hospitals\/\$\{hospitalId\}\/essence\/sources\/\$\{photo\.id\}\/public`/,
  )
  assert.doesNotMatch(photosSection, /asset_kind/)
})

test('세 섹션이 사실 섹션 뒤에 브랜드·사진·자기 도메인 순서로 붙는다', () => {
  const facts = infoPage.indexOf('<FactsSection')
  const brand = infoPage.indexOf('<BrandSection')
  const photos = infoPage.indexOf('<PhotosSection')
  const domain = infoPage.indexOf('<DomainSetupPanel')

  assert.ok(facts >= 0 && brand > facts, '브랜드 섹션이 사실 섹션 뒤에 없다')
  assert.ok(photos > brand, '사진 섹션이 브랜드 섹션 뒤에 없다')
  assert.ok(domain > photos, '자기 도메인이 사진 섹션 뒤에 없다')
  // 도메인 확인은 자동 폴링이다 — 사이트가 준비된 뒤에만 칸을 연다.
  assert.match(infoPage, /site_built && \(\s*<div id="domain-setup"/)
})
