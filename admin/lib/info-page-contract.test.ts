import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const infoPage = readFileSync(
  new URL('../app/hospitals/[id]/info/page.tsx', import.meta.url),
  'utf8',
)
const factsSection = readFileSync(
  new URL('../app/hospitals/[id]/info/FactsSection.tsx', import.meta.url),
  'utf8',
)
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
