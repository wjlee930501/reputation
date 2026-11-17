import assert from 'node:assert/strict'
import test from 'node:test'

import { CARE_PRINCIPLES, CARE_PRINCIPLES_TITLE, resolveCarePrinciples } from './clinic-template-copy.ts'
import { normalizeCarePrinciples } from './hospital-payload.ts'

test('병원 문구가 없으면 공통 기본 진료 원칙', () => {
  const resolved = resolveCarePrinciples(null)
  assert.equal(resolved.title, CARE_PRINCIPLES_TITLE)
  assert.deepEqual(
    resolved.cards.map((card) => card.title),
    CARE_PRINCIPLES.map((card) => card.title),
  )
})

test('바꾼 자리만 병원 문구, 비운 자리는 기본 카드', () => {
  const custom = normalizeCarePrinciples({
    title: '아픈 원인부터 확인합니다',
    lede: null,
    cards: [null, { title: '토·일요일에도 진료합니다', body: '토·일요일 오전에도 진료합니다.' }, null],
  })
  const resolved = resolveCarePrinciples(custom)
  assert.equal(resolved.title, '아픈 원인부터 확인합니다')
  assert.equal(resolved.cards[0].title, CARE_PRINCIPLES[0].title)
  assert.equal(resolved.cards[1].title, '토·일요일에도 진료합니다')
  assert.equal(resolved.cards[1].key, CARE_PRINCIPLES[1].key)
  assert.equal(resolved.cards[2].title, CARE_PRINCIPLES[2].title)
})

test('모양이 어긋난 병원 문구는 버리고 페이지는 기본 문구로 그린다', () => {
  assert.equal(normalizeCarePrinciples(undefined), null)
  assert.equal(normalizeCarePrinciples('문자열'), null)
  assert.equal(normalizeCarePrinciples({ title: '  ', cards: 'x' }), null)
  assert.deepEqual(normalizeCarePrinciples({ cards: [{ title: '제목만' }, 3] }), null)
  assert.deepEqual(normalizeCarePrinciples({ lede: ' 소개 ' }), { title: null, lede: '소개', cards: [null, null, null] })
})
