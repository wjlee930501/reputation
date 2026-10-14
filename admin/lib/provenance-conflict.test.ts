import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { createElement } from 'react'
import ReactDOMServer from 'react-dom/server'

import {
  ProvenanceConflict,
  formatConflictValue,
  provenanceConflictText,
} from './provenance-conflict.ts'

test('진료시간 충돌(요일 객체)도 화면이 깨지지 않고 한 줄로 그려진다', () => {
  // 서버는 진료시간 충돌을 객체로 저장한다 — 객체를 React 자식으로 넘기면 렌더가 실패했다.
  const html = ReactDOMServer.renderToStaticMarkup(
    createElement(ProvenanceConflict, {
      info: {
        origin: 'OPERATOR',
        conflict: { source: 'naver', value: { mon: '10:00-19:00', tue: '', sat: '09:00-13:00' } },
      },
    }),
  )
  assert.match(html, /data-testid="provenance-conflict"/)
  assert.match(html, /네이버에는 월 10:00-19:00, 토 09:00-13:00\(으\)로 되어 있습니다\./)
})

test('목록·진료 항목·문자열 충돌 값을 각각 읽을 수 있게 바꾼다', () => {
  assert.equal(formatConflictValue(['성동구', '광진구']), '성동구, 광진구')
  assert.equal(
    formatConflictValue([{ name: '치질 수술', description: null }, { name: '치루' }]),
    '치질 수술, 치루',
  )
  assert.equal(formatConflictValue('02-123-4567'), '02-123-4567')
  assert.equal(formatConflictValue(null), '')
  assert.equal(
    provenanceConflictText({ origin: 'AUTOFILL', conflict: { source: 'homepage', value: '02-1' } }),
    '홈페이지에는 02-1(으)로 되어 있습니다.',
  )
  assert.equal(provenanceConflictText({ origin: 'AUTOFILL' }), null)
  assert.equal(provenanceConflictText({ origin: 'AUTOFILL', conflict: { source: 'naver', value: {} } }), null)
})

test('정보 화면은 충돌 줄을 lib 렌더러로만 그리고 문구는 ADMIN_COPY에 있다', () => {
  const facts = readFileSync(
    new URL('../app/hospitals/[id]/info/FactsSection.tsx', import.meta.url),
    'utf8',
  )
  assert.match(facts, /import \{ PROVENANCE_SOURCE_LABEL, ProvenanceConflict \} from '@\/lib\/provenance-conflict'/)
  assert.doesNotMatch(facts, /\{conflict\.value\}/)
  assert.doesNotMatch(facts, /되어 있습니다/)
})

test('목록·진료 항목 칸에도 저장된 충돌 줄을 붙인다', () => {
  const facts = readFileSync(
    new URL('../app/hospitals/[id]/info/FactsSection.tsx', import.meta.url),
    'utf8',
  )
  for (const key of ['treatments', 'region', 'specialties', 'keywords', 'competitors']) {
    assert.match(facts, new RegExp(`ProvenanceConflict info=\\{provenance\\?\\.${key}\\}`), key)
  }
})
