import assert from 'node:assert/strict'
import test from 'node:test'

import {
  INFO_SECTION_ORDER,
  INFO_SECTION_TITLES,
  PHOTO_SOURCE_TYPE_OPTIONS,
  TEXT_SOURCE_TYPE_OPTIONS,
  assetKindForPhotoType,
  brandDefaultsNotice,
  groupNotesByType,
  infoSectionAnchorId,
  isPhotoSourceType,
  isTextSource,
  photoUploadFormData,
  processingSummary,
  remainingRequirementsSummary,
  sectionForRequirement,
  sourceIncidentHref,
  sourceRowStatus,
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
  // 좌표는 주소 저장이 자동 변환한다 — 사람이 채우는 지역 칸이 있는 섹션으로 보낸다.
  assert.equal(sectionForRequirement('geo'), 'targeting')
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

test('자료 상태는 사람이 누를 일이 아니라 지금 상태로만 말한다', () => {
  assert.deepEqual(sourceRowStatus({ status: 'PENDING' }), {
    label: '처리 대기 (자동)',
    tone: 'neutral',
  })
  assert.deepEqual(sourceRowStatus({ status: 'PROCESSED' }), { label: '처리 완료', tone: 'good' })
  assert.deepEqual(sourceRowStatus({ status: 'EXCLUDED' }), { label: '제외', tone: 'paused' })
  // ERROR는 자동 재시도가 끝난 자리다 — 하지 않는 일을 한다고 말하지 않는다.
  assert.deepEqual(sourceRowStatus({ status: 'ERROR' }), {
    label: '처리 실패 — 운영 센터 확인',
    tone: 'warn',
  })
  // 모르는 상태도 화면이 비지 않는다.
  assert.equal(sourceRowStatus({ status: 'WHATEVER' }).tone, 'neutral')
})

test('본문을 아직 못 받은 채널 자료는 처리 대기와 다른 단계로 말한다', () => {
  assert.deepEqual(
    sourceRowStatus({ status: 'PENDING', source_metadata: { fetch_state: 'QUEUED' } }),
    { label: '주소 내용 가져오는 중 (자동)', tone: 'neutral' },
  )
  assert.deepEqual(
    sourceRowStatus({ status: 'PENDING', source_metadata: { fetch_state: 'FAILED' } }),
    { label: '가져오기 재시도 대기', tone: 'warn' },
  )
  // 본문을 받은 뒤에는 다시 처리 대기다.
  assert.equal(
    sourceRowStatus({ status: 'PENDING', source_metadata: { fetch_state: 'FETCHED' } }).label,
    '처리 대기 (자동)',
  )
})

test('오류 상태에 실패 원문을 붙이지 않는다', () => {
  assert.equal(
    sourceRowStatus({ status: 'ERROR', process_error: 'TimeoutError' } as { status: string }).label,
    '처리 실패 — 운영 센터 확인',
  )
})

test('운영 센터 링크는 실제로 열린 예외가 있을 때만 만든다', () => {
  assert.equal(
    sourceIncidentHref('h-1', { status: 'ERROR', source_metadata: { incident_id: 'i-1' } }),
    '/operations?queue=incidents&hospital_id=h-1',
  )
  // 예외가 없으면 갈 곳도 없다 — 빈 화면으로 보내지 않는다.
  assert.equal(sourceIncidentHref('h-1', { status: 'ERROR' }), null)
  assert.equal(sourceIncidentHref('h-1', { status: 'ERROR', source_metadata: {} }), null)
  assert.equal(
    sourceIncidentHref('h-1', { status: 'PENDING', source_metadata: { incident_id: 'i-1' } }),
    null,
  )
})

test('근거 자료 표는 사진을 세지 않는다', () => {
  assert.ok(isTextSource({ source_type: 'HOMEPAGE' }))
  assert.ok(isTextSource({ source_type: 'NAVER_BLOG' }))
  assert.equal(isTextSource({ source_type: 'PHOTO_DOCTOR' }), false)
  assert.equal(isTextSource({ source_type: 'PHOTO_BRAND' }), false)
  for (const option of PHOTO_SOURCE_TYPE_OPTIONS) {
    assert.equal(isTextSource({ source_type: option.value }), false)
  }
  for (const option of TEXT_SOURCE_TYPE_OPTIONS) {
    assert.ok(isTextSource({ source_type: option.value }), `${option.value}가 빠졌다`)
  }
})

test('근거 노트는 정해진 순서로 묶이고 모르는 분류는 뒤에 붙는다', () => {
  const groups = groupNotesByType([
    { id: '1', note_type: 'KEY_MESSAGE' },
    { id: '2', note_type: 'NEW_KIND' },
    { id: '3', note_type: 'DOCTOR_PHILOSOPHY' },
    { id: '4', note_type: 'KEY_MESSAGE' },
  ])
  assert.deepEqual(
    groups.map((group) => group.noteType),
    ['DOCTOR_PHILOSOPHY', 'KEY_MESSAGE', 'NEW_KIND'],
  )
  assert.deepEqual(groups.map((group) => group.label), ['의료진 철학', '핵심 메시지', 'NEW_KIND'])
  assert.deepEqual(groups[1].notes.map((note) => note.id), ['1', '4'])
})

test('노트가 없으면 그룹도 없다', () => {
  assert.deepEqual(groupNotesByType([]), [])
})

test('진행 중인 처리만 한 줄로 알린다', () => {
  assert.equal(processingSummary({ state: 'RUNNING', total_count: 3 }), '자동 처리 중 3건')
  assert.equal(processingSummary({ state: 'QUEUED', total_count: 1 }), '자동 처리 중 1건')
  assert.equal(processingSummary({ state: 'REQUESTED', total_count: 2 }), '자동 처리 중 2건')
})

test('끝났거나 없는 처리는 알릴 것이 없다', () => {
  assert.equal(processingSummary(null), null)
  assert.equal(processingSummary({ state: 'SUCCEEDED', total_count: 3 }), null)
  assert.equal(processingSummary({ state: 'FAILED', total_count: 3 }), null)
  assert.equal(processingSummary({ state: null, total_count: 3 }), null)
  assert.equal(processingSummary({ state: 'RUNNING', total_count: 0 }), null)
})
