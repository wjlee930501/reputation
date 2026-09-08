import assert from 'node:assert/strict'
import test from 'node:test'

import {
  describeContentState,
  describeDomainState,
  describePublicService,
  humanRemaining,
  remainingConditionLabel,
  stateTone,
} from './hospital-states.ts'

test('public service labels follow the server kind and list only human work', () => {
  assert.deepEqual(describePublicService({ kind: 'live', remaining: [] }), { label: '공개 중', detail: null })
  assert.deepEqual(describePublicService({ kind: 'paused', remaining: [] }), { label: '일시 정지', detail: null })
  assert.deepEqual(
    describePublicService({ kind: 'not_live', remaining: [{ key: 'profile_complete', label: '필수 병원 정보 입력', actor: 'human', href: '/hospitals/h1/profile' }, { key: 'site_built', label: '공개 페이지 준비', actor: 'system', href: null }] }),
    { label: '준비 중', detail: '할 일: 필수 병원 정보 입력 · 시스템 처리 중: 공개 페이지 준비' },
  )
})

test('content state', () => {
  assert.equal(describeContentState({ kind: 'auto', remaining: [] }).label, '자동 발행 중')
  assert.equal(describeContentState({ kind: 'exception', remaining: [] }).label, '예외 있음')
  assert.equal(describeContentState({ kind: 'preparing', remaining: [{ key: 'sources', label: '근거 자료 처리 2건', actor: 'system', href: null }] }).detail, '시스템 처리 중: 근거 자료 처리 2건')
})

test('domain state is silent without a custom domain', () => {
  assert.equal(describeDomainState({ kind: 'unused' }), null)
  assert.equal(describeDomainState({ kind: 'problem', reason: '인증서 발급 실패' })?.label, '문제: 인증서 발급 실패')
  assert.equal(stateTone('exception'), 'warn')
})

// 마지막 확인만 말하고 응답이 정상이었는지 빼면, 실패한 관측과 성공한 관측이 같아 보인다.
test('the last check says whether the address answered', () => {
  const checked = '2026-09-08T01:23:00Z'
  assert.match(
    describeDomainState({ kind: 'connected', last_checked_at: checked, last_check_ok: true })?.detail ?? '',
    /응답 정상$/,
  )
  assert.match(
    describeDomainState({ kind: 'problem', reason: 'DNS 확인 실패', last_checked_at: checked, last_check_ok: false })?.detail ?? '',
    /응답 실패$/,
  )
})

// 자료가 없는 것과 자동 검수가 도는 것은 다른 일이고, 멈춘 병원은 자동 발행 중이 아니다.
test('zero sources and a stopped service are human-readable conditions', () => {
  assert.deepEqual(
    describeContentState({ kind: 'preparing', remaining: ['sources_required'] }),
    { label: '준비 중', detail: '할 일: 공식 채널·근거 자료 등록' },
  )
  assert.deepEqual(
    describeContentState({ kind: 'preparing', remaining: ['service_paused'] }),
    { label: '준비 중', detail: '할 일: 서비스 재개' },
  )
  assert.deepEqual(
    describeContentState({ kind: 'preparing', remaining: ['public_service'] }),
    { label: '준비 중', detail: '시스템 처리 중: 공개 서비스 시작 후 자동 발행' },
  )
  assert.deepEqual(humanRemaining(['sources_required', 'public_service']).map((c) => c.key), [
    'sources_required',
  ])
})

// 목록 행은 같은 조건을 키 문자열로만 받는다 — 헤더와 목록이 다른 문구를 쓰면
// 같은 병원의 남은 일이 화면마다 달라 보인다.
test('list rows carry the same conditions as bare keys', () => {
  assert.equal(remainingConditionLabel('sources:2'), '근거 자료 처리 2건')
  assert.equal(remainingConditionLabel('essence_review'), '콘텐츠 운영 기준 자동 검수')
  assert.equal(remainingConditionLabel('schedule'), '발행 요일 설정')
  assert.deepEqual(
    describePublicService({ kind: 'not_live', remaining: ['profile_complete', 'site_built'] }),
    { label: '준비 중', detail: '할 일: 필수 병원 정보 입력 · 시스템 처리 중: 공개 페이지 준비' },
  )
  assert.equal(
    describeContentState({ kind: 'preparing', remaining: ['schedule', 'sources:2'] }).detail,
    '할 일: 발행 요일 설정 · 시스템 처리 중: 근거 자료 처리 2건',
  )
})

test('only human conditions become links', () => {
  const remaining = [
    { key: 'profile_complete', label: '필수 병원 정보 입력', actor: 'human' as const, href: '/hospitals/h1/profile' },
    { key: 'site_built', label: '공개 페이지 준비', actor: 'system' as const, href: null },
  ]
  assert.deepEqual(humanRemaining(remaining).map((c) => c.key), ['profile_complete'])
  assert.deepEqual(humanRemaining(['sources:2', 'essence_review']), [])
})

test('tones separate paused from not-live and problems from waiting', () => {
  assert.equal(stateTone('live'), 'good')
  assert.equal(stateTone('connected'), 'good')
  assert.equal(stateTone('paused'), 'paused')
  assert.equal(stateTone('problem'), 'warn')
  assert.equal(stateTone('not_live'), 'neutral')
  assert.equal(stateTone('checking'), 'neutral')
  assert.equal(stateTone('unused'), 'neutral')
})
