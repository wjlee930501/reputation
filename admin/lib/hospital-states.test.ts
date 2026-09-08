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
