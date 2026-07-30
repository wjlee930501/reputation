import assert from 'node:assert/strict'
import test from 'node:test'

import { heroScarcity } from './landing-copy.ts'
import { parseSlotStatus, resolveSlotState, type SlotStatus } from './diagnosis-slots.ts'

/**
 * 선착순 표시의 값어치는 **실제 카운터라는 점**에 전부 있다.
 * 숫자를 만들어내거나, 마감인데 자리가 있는 것처럼 보이면 장치 자체가 거짓이 된다.
 */

const OK = { date: '2026-07-30', total: 20, used: 1, remaining: 19 }

// ── 응답 파싱 ───────────────────────────────────────────────────────
test('a well-formed payload is accepted', () => {
  assert.deepEqual(parseSlotStatus(OK), OK)
})

test('a payload whose parts do not add up is rejected', () => {
  // used + remaining ≠ total 이면 우리가 이해하지 못하는 응답이다. 반쯤 맞는
  // 카운터를 보여주는 것은 없는 것보다 나쁘다.
  assert.equal(parseSlotStatus({ ...OK, remaining: 5 }), null)
})

test('non-numeric or negative counts are rejected', () => {
  assert.equal(parseSlotStatus({ ...OK, remaining: '19' }), null)
  assert.equal(parseSlotStatus({ ...OK, used: -1, remaining: 21 }), null)
  assert.equal(parseSlotStatus({ ...OK, remaining: 18.5 }), null)
})

test('a missing date or zero total is rejected', () => {
  assert.equal(parseSlotStatus({ ...OK, date: '' }), null)
  assert.equal(parseSlotStatus({ date: '2026-07-30', total: 0, used: 0, remaining: 0 }), null)
})

test('junk is rejected instead of throwing', () => {
  for (const junk of [null, undefined, 'ok', 42, [], { ok: false }]) {
    assert.equal(parseSlotStatus(junk), null)
  }
})

// ── 문구 결정 ───────────────────────────────────────────────────────
test('an open day shows the real remaining count', () => {
  const state = resolveSlotState(OK, heroScarcity)
  assert.equal(state.tone, 'is-open')
  assert.match(state.text, /19/)
  // 템플릿 자리표시자가 남아 있으면 치환이 실패한 것이다.
  assert.doesNotMatch(state.text, /\{remaining\}/)
})

test('a full day says closed and offers no count', () => {
  const state = resolveSlotState({ ...OK, used: 20, remaining: 0 }, heroScarcity)
  assert.equal(state.tone, 'is-closed')
  assert.match(state.text, /마감/)
  assert.doesNotMatch(state.text, /남았/)
})

test('an unreadable counter never invents a number', () => {
  /**
   * 가장 중요한 갈래다. 카운터를 못 읽었을 때 20을 기본값으로 넣으면 "실제 카운터"라는
   * 약속이 조용히 깨지고, 이미 마감된 날에도 자리가 있는 것처럼 보인다.
   */
  const state = resolveSlotState(null, heroScarcity)
  assert.equal(state.tone, 'is-unknown')
  assert.doesNotMatch(state.text, /남았|남은/)
  assert.equal(state.text, heroScarcity.fallback)
})

test('only the open state gets the accent tone', () => {
  // 마감·미확인을 accent로 칠하면 색이 주는 신호가 뒤집힌다.
  const tones = [
    resolveSlotState(OK, heroScarcity).tone,
    resolveSlotState({ ...OK, used: 20, remaining: 0 }, heroScarcity).tone,
    resolveSlotState(null, heroScarcity).tone,
  ]
  assert.deepEqual(tones, ['is-open', 'is-closed', 'is-unknown'])
})

test('a single remaining slot still reads as open', () => {
  // 경계값 — 1은 남은 것이다. 0 이하만 마감이다.
  const state = resolveSlotState({ ...OK, used: 19, remaining: 1 } as SlotStatus, heroScarcity)
  assert.equal(state.tone, 'is-open')
  assert.match(state.text, /1분|1곳/)
})
