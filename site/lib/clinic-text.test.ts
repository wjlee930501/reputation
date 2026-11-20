import assert from 'node:assert/strict'
import test from 'node:test'

import { innerKeepSegments, keepTogetherSegments } from './clinic-text.ts'

const kept = (text: string, compounds = false) =>
  keepTogetherSegments(text, { compounds }).filter((s) => s.keep).map((s) => s.text)

test('segments always join back to the original text', () => {
  for (const text of [
    '09:00 ~ 21:00 (점심 13:30 ~ 14:30)',
    '08:30 ~ 18:30 (점심 13:00 ~ 14:00, 접수 마감 18:00)',
    '평일은 08:30~18:30(점심 13:00~14:00)입니다',
    '평일 밤 9시까지, 주말·공휴일에도 진료합니다',
    '전화 053-801-3007로 상담',
    '',
  ]) {
    assert.equal(keepTogetherSegments(text, { compounds: true }).map((s) => s.text).join(''), text)
  }
})

test('a time range and a short lunch note each stay on one line', () => {
  // 기본형 오늘 진료 칸: '09:00 〜 21:00 (점심 13:30 〜 / 14:30)'로 끊겼다.
  assert.deepEqual(kept('09:00 ~ 21:00 (점심 13:30 ~ 14:30)'), ['09:00 ~ 21:00', '(점심 13:30 ~ 14:30)'])
  assert.deepEqual(kept('08:00 ~ 13:00 (점심시간 없음)'), ['08:00 ~ 13:00', '(점심시간 없음)'])
})

test('a long note breaks only after its comma', () => {
  // B안 오늘 진료 띠: '(점심 13:00 ~ / 14:00 …)'로 끊겼다.
  assert.deepEqual(kept('08:30 ~ 18:30 (점심 13:00 ~ 14:00, 접수 마감 18:00)'), [
    '08:30 ~ 18:30',
    '(점심 13:00 ~ 14:00,',
    '접수 마감 18:00)',
  ])
})

test('ranges written without spaces and phone numbers are kept whole', () => {
  assert.deepEqual(kept('평일은 08:30~18:30(점심 13:00~14:00)입니다'), ['08:30~18:30', '(점심 13:00~14:00)'])
  assert.deepEqual(kept('전화 053-801-3007, 1588-1234'), ['053-801-3007,', '1588-1234'])
})

test('middle-dot words are kept whole only where asked (large titles)', () => {
  // 기본형 첫 화면 제목: '평일 밤 9시까지, 주말· / 공휴일에도 진료합니다'.
  assert.deepEqual(kept('평일 밤 9시까지, 주말·공휴일에도 진료합니다', true), ['주말·공휴일에도'])
  assert.deepEqual(kept('토·일·공휴일 09:00~13:00에 진료합니다.', true), ['토·일·공휴일', '09:00~13:00'])
  assert.deepEqual(kept('평일 밤 9시까지, 주말·공휴일에도 진료합니다'), [])
})

test('a short note with a comma also breaks after the comma, not inside', () => {
  // 365서귀포 모바일 반 폭 칸: '(점심시간 없음, 접수 마감 12:30)'을 통째로 묶으면 칸을 넘쳤다.
  assert.deepEqual(kept('08:30 ~ 13:00 (점심시간 없음, 접수 마감 12:30)'), [
    '08:30 ~ 13:00',
    '(점심시간 없음,',
    '접수 마감 12:30)',
  ])
})

test('very long pieces are not kept together', () => {
  const longNote = '(평일 점심시간 없이 진료하며 토요일과 공휴일은 오전만 진료하고 일요일은 휴진합니다)'
  assert.deepEqual(kept(longNote), [])
})

test('inside a kept note, the time range is kept again so a narrow cell breaks before it', () => {
  assert.deepEqual(innerKeepSegments('(점심 13:00 ~ 14:00,'), [
    { text: '(점심 ', keep: false },
    { text: '13:00 ~ 14:00,', keep: true },
  ])
  assert.deepEqual(innerKeepSegments('08:30 ~ 18:30'), [{ text: '08:30 ~ 18:30', keep: false }])
  assert.deepEqual(innerKeepSegments('(점심시간 없음,'), [{ text: '(점심시간 없음,', keep: false }])
})
