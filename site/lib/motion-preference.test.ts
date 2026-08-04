import assert from 'node:assert/strict'
import test, { afterEach } from 'node:test'

import {
  MOTION_ATTRIBUTE,
  MOTION_PAUSED,
  MOTION_RUNNING,
  isMotionAllowed,
  readMotionState,
  setMotionState,
  subscribeMotionState,
} from './motion-preference.ts'

// 이 모듈은 document/window를 직접 읽는다. 여기서는 최소한의 대역을 세워
// "무엇을 진실로 삼는가"만 검증한다 — 진짜 브라우저 동작이 아니라 규약이 대상이다.
type Listener = () => void

function installDom(options: { reduced?: boolean } = {}) {
  const attributes = new Map<string, string>()
  const listeners = new Map<string, Listener[]>()

  const globals = globalThis as unknown as Record<string, unknown>
  globals.document = {
    documentElement: {
      getAttribute: (name: string) => attributes.get(name) ?? null,
      setAttribute: (name: string, value: string) => attributes.set(name, value),
    },
  }
  globals.window = {
    matchMedia: () => ({ matches: Boolean(options.reduced) }),
    dispatchEvent: (event: { type: string }) => {
      for (const fn of listeners.get(event.type) ?? []) fn()
      return true
    },
    addEventListener: (type: string, fn: Listener) => {
      listeners.set(type, [...(listeners.get(type) ?? []), fn])
    },
    removeEventListener: (type: string, fn: Listener) => {
      listeners.set(type, (listeners.get(type) ?? []).filter((f) => f !== fn))
    },
  }
  globals.CustomEvent = class {
    type: string
    constructor(type: string) {
      this.type = type
    }
  }
  return { attributes }
}

afterEach(() => {
  const globals = globalThis as unknown as Record<string, unknown>
  delete globals.document
  delete globals.window
  delete globals.CustomEvent
})

test('motion runs by default and pauses only when asked', () => {
  installDom()

  assert.equal(readMotionState(), MOTION_RUNNING)
  assert.equal(isMotionAllowed(), true)

  setMotionState(MOTION_PAUSED)
  assert.equal(readMotionState(), MOTION_PAUSED)
  assert.equal(isMotionAllowed(), false)
})

test('the OS setting outranks the page toggle', () => {
  // 모션을 줄인 사용자에게는 페이지의 재생 버튼이 있어도 돌리지 않는다.
  installDom({ reduced: true })
  setMotionState(MOTION_RUNNING)

  assert.equal(isMotionAllowed(), false)
})

test('the paused state is written where CSS can see it', () => {
  // 질문 띠는 서버 컴포넌트라 JS로 멈출 수 없다 — CSS가 읽을 속성이 유일한 통로다.
  const { attributes } = installDom()

  setMotionState(MOTION_PAUSED)

  assert.equal(attributes.get(MOTION_ATTRIBUTE), MOTION_PAUSED)
})

test('subscribers are notified and can unsubscribe', () => {
  installDom()
  let calls = 0
  const unsubscribe = subscribeMotionState(() => {
    calls += 1
  })

  setMotionState(MOTION_PAUSED)
  setMotionState(MOTION_RUNNING)
  assert.equal(calls, 2)

  unsubscribe()
  setMotionState(MOTION_PAUSED)
  assert.equal(calls, 2, '해제한 뒤에는 더 이상 호출되면 안 된다')
})

test('server-side rendering never claims motion is allowed', () => {
  // document가 없는 렌더 경로에서 true를 돌려주면 타이머가 서버에서 시작된다.
  assert.equal(isMotionAllowed(), false)
  assert.equal(readMotionState(), MOTION_RUNNING)
})
