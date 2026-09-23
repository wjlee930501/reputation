import assert from 'node:assert/strict'
import test from 'node:test'

import { headingAccents, splitAccent } from './landing-accent.ts'

test('every accent phrase is part of the heading it colors', () => {
  // 카피를 고치면서 구간을 안 고치면 칠이 조용히 빠진다. 여기서 먼저 걸리게 한다.
  for (const { text, accent } of headingAccents) {
    assert.ok(text.includes(accent), `"${accent}"가 제목 "${text}"에 없습니다.`)
  }
})

test('an accent never swallows the whole heading', () => {
  // 제목 전체를 칠하면 강조가 아니라 색만 바뀐 제목이다.
  for (const { text, accent } of headingAccents) {
    assert.ok(accent.length < text.length, `"${text}" 전체가 칠해집니다.`)
  }
})

test('splitAccent keeps the original text intact', () => {
  for (const { text } of headingAccents) {
    const parts = splitAccent(text)
    assert.ok(parts)
    assert.equal(parts.before + parts.accent + parts.after, text)
  }
})

test('splitAccent falls back to plain text when the phrase is gone', () => {
  assert.equal(splitAccent('사라진 제목입니다'), null)
  assert.equal(splitAccent('제목입니다', '없는 구간'), null)
})
