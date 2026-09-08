import assert from 'node:assert/strict'
import test from 'node:test'

import {
  INFO_SECTION_ORDER,
  INFO_SECTION_TITLES,
  PHOTO_SOURCE_TYPE_OPTIONS,
  assetKindForPhotoType,
  brandDefaultsNotice,
  infoSectionAnchorId,
  isPhotoSourceType,
  photoUploadFormData,
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

test('브랜드 값이 다 차 있으면 알릴 것이 없다', () => {
  assert.equal(brandDefaultsNotice([]), null)
})

test('비어 있는 브랜드 값은 승인 대기가 아니라 기본값 사용 중이다', () => {
  assert.equal(
    brandDefaultsNotice(['공식 로고', '대표색 1개']),
    '공개 페이지 기본값 사용 중: 공식 로고, 대표색 1개 — 바꾸려면 입력',
  )
  // 승인이라는 말도, 세어야 할 건수도 만들지 않는다.
  assert.doesNotMatch(brandDefaultsNotice(['공식 로고']) ?? '', /승인|\d+건/)
})

test('허용 용도는 사진 유형에서 나온다 — 업로드할 때 따로 묻지 않는다', () => {
  assert.equal(assetKindForPhotoType('PHOTO_DOCTOR'), 'VERIFIED_REAL_PERSON')
  for (const option of PHOTO_SOURCE_TYPE_OPTIONS.filter((o) => o.value !== 'PHOTO_DOCTOR')) {
    assert.equal(assetKindForPhotoType(option.value), 'VERIFIED_FACILITY')
  }
})

test('사진 유형 판정은 업로드 선택지와 같은 목록을 쓴다', () => {
  assert.ok(isPhotoSourceType('PHOTO_CLINIC_EXTERIOR'))
  assert.equal(isPhotoSourceType('HOMEPAGE'), false)
})

test('사진 업로드 한 요청에 공개 여부와 권리 3필드가 함께 실린다', () => {
  const body = photoUploadFormData({
    sourceType: 'PHOTO_DOCTOR',
    title: '  원장 프로필  ',
    file: new Blob(['x']),
    isPublic: true,
    owner: '  모션랩스의원  ',
    basis: 'LICENSE',
    reference: '  촬영 계약 3조  ',
  })

  assert.deepEqual([...body.keys()].sort(), [
    'asset_kind',
    'file',
    'is_public',
    'photo_evidence_reference',
    'photo_rights_basis',
    'photo_source_owner',
    'source_type',
    'title',
  ])
  assert.equal(body.get('is_public'), 'true')
  assert.equal(body.get('asset_kind'), 'VERIFIED_REAL_PERSON')
  assert.equal(body.get('photo_source_owner'), '모션랩스의원')
  assert.equal(body.get('photo_rights_basis'), 'LICENSE')
  assert.equal(body.get('photo_evidence_reference'), '촬영 계약 3조')
  assert.equal(body.get('title'), '원장 프로필')
})

test('공개하지 않기로 한 사진도 권리 정보를 그대로 보낸다', () => {
  const body = photoUploadFormData({
    sourceType: 'PHOTO_CLINIC_INTERIOR',
    title: '대기실',
    file: new Blob(['x']),
    isPublic: false,
    owner: '모션랩스의원',
    basis: 'OWNER_CONSENT',
    reference: '동의서.pdf',
  })

  assert.equal(body.get('is_public'), 'false')
  assert.equal(body.get('photo_evidence_reference'), '동의서.pdf')
})
