import assert from 'node:assert/strict'
import test from 'node:test'

import {
  INFO_SECTION_ORDER,
  INFO_SECTION_TITLES,
  infoSectionAnchorId,
  remainingRequirementsSummary,
  sectionForRequirement,
} from './info-sections.ts'

test('빈 목록은 남은 항목이 아니라 완료로 말한다', () => {
  assert.equal(remainingRequirementsSummary([]), '필수 항목 입력 완료')
})

test('남은 항목은 개수를 먼저 말하고 라벨을 잇는다', () => {
  assert.equal(
    remainingRequirementsSummary([
      { key: 'director_basic', label: '원장명·약력' },
      { key: 'treatments', label: '진료 항목' },
    ]),
    '남은 필수 항목 2개 · 원장명·약력, 진료 항목',
  )
})

test('서버 필수 항목 키 8개가 모두 섹션을 가진다', () => {
  assert.equal(sectionForRequirement('director_basic'), 'director')
  assert.equal(sectionForRequirement('director_philosophy'), 'director')
  assert.equal(sectionForRequirement('contact'), 'contact')
  assert.equal(sectionForRequirement('web_channels'), 'channels')
  assert.equal(sectionForRequirement('ai_channels'), 'channels')
  assert.equal(sectionForRequirement('geo'), 'channels')
  assert.equal(sectionForRequirement('targeting'), 'targeting')
  assert.equal(sectionForRequirement('treatments'), 'treatments')
})

test('모르는 키도 갈 곳이 있다', () => {
  assert.equal(sectionForRequirement('unknown_key'), 'director')
})

test('섹션 순서와 제목', () => {
  assert.deepEqual(INFO_SECTION_ORDER, [
    'director',
    'treatments',
    'contact',
    'channels',
    'targeting',
  ])
  assert.deepEqual(
    INFO_SECTION_ORDER.map((section) => INFO_SECTION_TITLES[section]),
    ['병원·원장', '진료 항목', '연락처·진료시간', '공식 채널', '운영 기준 정보'],
  )
})

test('앵커 id는 섹션 이름을 그대로 쓴다', () => {
  assert.equal(infoSectionAnchorId('channels'), 'info-channels')
})
